import copy
import inspect
import os
import signal
from datetime import datetime
import math
import time
import random
import tracemalloc
import psutil
import multiprocessing as mp

from mctsNode import MctsNode
from supervisor import Supervisor


class DshieldPlanner:

    def __init__(self, settings):

        # Config params
        self.settings = settings  # Example: {"objective": app.objectiveFn, "rolloutLimit": 10000, "timeLimit": 15}
        self.rolloutLimit = settings["rolloutLimit"] if "rolloutLimit" in settings else None
        self.processCount = settings["processCount"] if "processCount" in settings else 1
        self.plannerTimeLimitSeconds = settings["timeLimit"] if "timeLimit" in settings else None
        self.randomSeed = 3
        self.useSharedNodes = False
        self.sharedNodes = None
        self.sharedNodesLock = None

        # Internal initialization
        self.root = None
        self.nextNodeId = 0
        self.openNodes = []
        self.allNodes = {}
        self.currentNode = None # newest child node, used by setNodeChoices and updateState
        self.stats = {}
        self.rolloutStats = {}
        self.stage =  "select"   #select, replay, expand, simulate, backpropagate
        self.mostPlayedMove = None
        self.replayPlan = None
        self.replayNodeToExpand = None
        random.seed(self.randomSeed)
        self.randomChoicePct = None # set in each parallel process
        self.randomChoiceCount = 0
        self.totalChoiceCount = 0

        # Multiprocessing initialization
        manager = mp.Manager()
        self.appToPlannerQ     = manager.Queue()
        self.appToControllerQ  = manager.Queue()
        self.supToExecQ        = manager.Queue()
        self.parallelResults = manager.list()
        self.sharedDict = manager.dict()
        self.loggerQ = manager.Queue()
        self.logLock = manager.RLock()
        self.planner         = None
        self.plannerPid = 0
        self.plannerProc = None

        self.mode = None # set by supervisors
        self.pid = os.getpid()

        self.execPid = 0
        self.loggerPid = 0

        # Result data
        self.bestPlanNode = None
        self.bestPlanScore = 0
        self.bestPlanState = None
        print("Planner settings: "+str(self.settings))

    def start(self, appMethod):
        self.initStats()
        loggerProc = mp.Process(target=self.loggerMsgHandler, args=(self.loggerQ,))
        loggerProc.start()

        self.targetProcs = [appMethod.__name__]
        self.appMethod = appMethod

        # start planner
        self.planner = Supervisor(self, "planning",  self.logLock, self.loggerQ, self.sharedDict)
        plannerProc = self.planner.start(self.appToPlannerQ)

        self.sendStartMsgToPlanner()
        self.logMsg("planner: "+str(self.planner))
        plannerProc.join()
        self.bestPlanState = self.sharedDict["bestPlanState"]
        self.printStats()
        self.loggerQ.put("LOGGER_EXIT")
        # print(str(self.bestPlanState))


    def sendStartMsgToPlanner(self):
        self.logMsg("sendStartMsgToPlanner() starting planner")
        msg = {"type": "start", "execPid": os.getpid()}
        self.appToPlannerQ.put(msg)

    def parallelMCTS(self, applicationMethod, processCount):
        startTimestamp = self.timestamp()
        startTime = time.time()
        self.logMsg("\nparallelMCTS() processCount: "+str(processCount)+", sharedNodes: "+str(self.useSharedNodes)+", start time: "+startTimestamp)
        procs = []
        randomChoicePct = 0
        pctIncrement = math.ceil(100/processCount)
        manager = mp.Manager()
        self.sharedNodes = manager.list()
        self.sharedNodesLock = manager.RLock()
        if self.useSharedNodes:
            self.createRootNode()
        for i in range(processCount):
            if self.settings["greedy"] or self.settings["allGreedy"]:
                p = mp.Process(target=self.mcts, args=(applicationMethod,self.parallelResults, randomChoicePct, self.sharedNodes, self.sharedNodesLock))
            else:
                p = mp.Process(target=self.mcts, args=(applicationMethod,self.parallelResults, 100, self.sharedNodes, self.sharedNodesLock))
            p.start()
            procs.append(p)
            if self.settings["greedy"] and not self.settings["allGreedy"]:
                randomChoicePct = min(randomChoicePct + pctIncrement, 100)
        self.logMsg("parallelMCTS() waiting for processes to finish")
        for p in procs:
            p.join()
        # self.printTree()
        self.bestPlanScore = 0
        for treeResult in self.parallelResults:
            treeBestScore = treeResult["bestScore"]
            if treeBestScore > self.bestPlanScore:
                self.bestPlanScore = treeBestScore
                self.bestPlanState = treeResult["bestState"]
        self.printParallelResults()
        elapsedTime = round(time.time() - startTime, 3)
        self.logMsg("parallelMCTS() done. Start: "+startTimestamp+", end "+self.timestamp()+", elapsed: "+str(elapsedTime), True)

    def mcts(self, applicationMethod, parallelResults, randomChoicePct, sharedNodes, sharedNodesLock):
        self.randomChoicePct = randomChoicePct
        self.sharedNodes = sharedNodes
        self.sharedNodesLock = sharedNodesLock
        self.logMsg("\nmcts() rollout limit: "+str(self.rolloutLimit) +", randomChoicePct: "+str(self.randomChoicePct))
        if self.useSharedNodes:
            self.root = self.sharedNodes[0]
            self.logMsg("mcts: root: "+str(self.root)+", sharedRoot: "+str(self.sharedNodes[0]))
        else:
            self.createRootNode()

        # do rollouts
        rolloutCount = 1
        while rolloutCount <= self.rolloutLimit:
            self.doRollout(rolloutCount, applicationMethod)
            rolloutCount += 1

        # print results
        print("random choices: "+str(self.randomChoiceCount)+"/"+str(self.totalChoiceCount)+" = "+str(round(self.randomChoiceCount/self.totalChoiceCount, 3)))

        # For "execution", incrementally return single next best move (not used)
        self.collectParallelResults(parallelResults)

        # self.getMostPlayedNextMove()
        # bestMove = self.mostPlayedMove.priorMove
        # self.logMsg("bestMove: "+str(bestMove))

    def doRollout(self, rolloutNumber, applicationMethod):
        rolloutStart = time.time()
        self.logMsg("\n=========\nRollout "+str(rolloutNumber))
        self.setStage("select")
        self.currentNode = None
        applicationMethod()  # run user application-level code
        score = self.rolloutScore()
        self.updateTree(self.currentNode, score)
        self.stopRolloutStats(rolloutStart, rolloutNumber)



    def chooseValue(self, choicesDict, choiceSorter=None):
        # Select choice from parentChoices and update MCTS stage as necessary
        # Return next choice on each call (like a generator)
        # Handle special cases
        # self.logMsg("chooseValue() choicesDict: "+str(choicesDict))

        choices = choicesDict["choices"]
        varName = choicesDict["varName"]
        # self.logMsg("chooseValue() varName: "+str(varName))
        if not choices:
            return None
        elif len(choices) == 1:
            return choices[0]
        # Multiple choices exist so do MCTS
        nodeToExpand = None
        if self.stage == "select":
            # select leaf node and update stage to replay or expand
            if self.useSharedNodes:
                self.sharedNodesLock.acquire()  # released by expandLeaf before starting simulate stage
                # self.logMsg("acquire lock")

            nodeToExpand = self.selectLeaf(varName)
        if self.stage == "replay":
            # choose next move in replay plan if selected node is not root
            choice = self.replay(choices, varName)
        elif self.stage == "expand":
            # expand the selected leaf node
            if not nodeToExpand and self.replayNodeToExpand:
                self.logMsg("expand() continue after replay plan")
                nodeToExpand = self.replayNodeToExpand
            choice = self.expandLeaf(nodeToExpand, choicesDict, choiceSorter)
        elif self.stage == "simulate":
            # simulate remaining choices in rollout
            choice = self.simulate(choicesDict, choiceSorter)
        return choice

    def selectLeaf(self, varName):
        # descend tree (iteratively) to find a leaf (node with unexplored choices).
        # return choice (edge label) after traversing each edge
        # sets stage to expand if selected node is root otherwise sets stage to replay
        # sets replay plan if selected is not root
        self.logMsg("selectLeaf() varName: "+str(varName))

        node = self.root
        if self.useSharedNodes:
            node = self.sharedNodes[0]
            self.root = node
        else:
            node = self.root
        # print("root: "+str(node))
        while node.hasChildren() and not node.isLeaf():
            nodeId = self.getBestChild(node)
            node = self.getNode(nodeId) # refetch node from sharedNodes
        selectedNode = node if node.isLeaf() else None
        self.logMsg("selectLeaf() varName: "+str(varName)+", selected node: "+str(selectedNode))

        if selectedNode:
            if selectedNode.id != self.root.id:
                self.collectReplayPlan(selectedNode)
                node = self.getNode(nodeId)
                self.replayNodeToExpand = node
                self.currentNode = node
                self.setStage("replay")
            else:
                self.setStage("expand") # continue to next stage
        else:
            self.logMsg("selectLeaf() Search complete! All nodes have been explored.")
            self.setStage("exhausted")
        return selectedNode

    def replay(self, choices, varName):
        self.logMsg("replay() varName: "+str(varName)+", choices: "+str(choices)+", replayNodeToExpand: "+str(self.replayNodeToExpand))
        if self.replayNodeToExpand.status == "init":
            # set choices for node before next select stage
            self.setNodeChoices(self.replayNodeToExpand, choices)
        choice = self.getNextReplayMove()
        # self.logMsg("replay() choices: "+str(choices) +" stage: "+str(self.stage) + ", Replay choice: "+str(choice))
        if choice not in choices:
            self.logMsg("BUG!! varName: "+str(varName) +", replayNodeToExpand: "+str(self.replayNodeToExpand)+", replayPlan: "+str(self.replayPlan))
            self.logMsg("replay() choices: "+str(choices) +" stage: "+str(self.stage) + ", Replay choice: "+str(choice))
            self.printPathFromRoot(self.replayNodeToExpand)
        assert choice in choices, "ERROR! replay choice "+str(choice) + " not in choices: "+str(choices)
        if not self.replayPlan:
            self.setStage("expand")
        return choice

    def expandLeaf(self, node, choicesDict, choiceSorter):
        self.logMsg("expandLeaf() node: "+str(node)+", choicesDict: "+str(choicesDict))
        # randomly select an unexplored  choice
        choices = choicesDict["choices"]
        if node.status == "init":
            # set choices for node before next select stage
            self.setNodeChoices(node, choices)

        # TODO: BUG? Why does node.unexploredChoices != choices
        choices = node.unexploredChoices
        choicesDict["choices"] = choices
        if choiceSorter:
            if choiceSorter == "random":
                choice = random.choice(choices)
                self.totalChoiceCount += 1
                self.randomChoiceCount += 1
            else:
                # TODO: handle greedy randomChoicePct
                choice = choiceSorter(choicesDict)[0]
        else:
            choice = choices[0] # default to the choices order
        node.unexploredChoices.remove(choice)
        self.updateSharedNode(node)
        self.logMsg("expandLeaf() "+str(node) +" with choice "+str(choice))
        if not node.isLeaf():
            # all choices have been explored
            node.status = "exhausted"
        child = self.createChildNode(node, choicesDict["varName"], choice)
        self.setCurrentNode(child) # remember child to set choices on next call to chooseValue
        self.logMsg("expandLeaf() choices: "+str(choices) +" stage: "+str(self.stage)+", choice: "+str(choice))
        self.setStage("simulate")
        if self.useSharedNodes:
            # self.logMsg("release lock")
            self.sharedNodesLock.release()
        return choice

    def simulate(self, choicesDict, choiceSorter):
        choices = choicesDict["choices"]
        if self.currentNode.status == "init":
            # set choices for node before next select stage
            self.setNodeChoices(self.currentNode, choices)
        if choiceSorter and choiceSorter != "random":
            choice = None
            self.totalChoiceCount += 1
            if self.randomChoicePct > 0:
                diceRoll = random.randrange(101)
                if diceRoll <= self.randomChoicePct:
                    # select random choice
                    choice = random.choice(choices)
                    self.randomChoiceCount += 1
                    # print("Random Choice %: "+str(self.randChoicePct) + ", diceRoll: "+str(diceRoll)+", random choice: "+str(choice))
            if not choice:
                choice = choiceSorter(choicesDict)[0]
        else:
            self.totalChoiceCount += 1
            self.randomChoiceCount += 1
            choice = random.choice(choices)
        # self.logMsg("simulate() choice: "+str(choice) +", choices: "+str(choices))
        return choice

    def getBestChild(self, parent):
        # self.printTree()
        # calculate normalized scores (ranks) for each existing child
        childRewards = [] # avgObjectiveFunction Rewards

        # for each child in visited children
        for childId in parent.children:
            child = self.getNode(childId)
            childRewards.append((childId, child.avgReward))

        # sort children from bad to good
        # more promising children get higher score (rank)
        childRewards.sort(key = lambda x: x[1])
        rank = 1
        childRanks = {}  # child ranks
        childRankTotal = 0
        for childPair in childRewards:
            childId = childPair[0]
            childRanks[childId] = rank
            childRankTotal += rank
            rank += 1
        normalizedScores = {}
        for childId in childRanks:
            # TODO: should denominator be maxChildScore?
            normalizedScores[childId] = childRanks[childId]/childRankTotal

        # calculate UCT scores for each child
        c = math.sqrt(2)
        parent.visitCount = max(parent.visitCount, 1)  # TODO: fix this hack (why does visitCount = 0?)
        parentVisits = 2 * math.log(parent.visitCount)
        uctScores = []
        for childId in normalizedScores:
            child = self.getNode(childId)
            child.visitCount = max(child.visitCount,1) # TODO: fix this hack (why does visitCount = 0?)
            uctScore = normalizedScores[childId] + (c * math.sqrt(parentVisits/child.visitCount))
            uctScores.append((childId,uctScore))

        # choose child with best score
        uctScores.sort(key = lambda x: x[1], reverse=True)
        winner = uctScores[0]
        return winner[0]

    def rolloutScore(self):
        # called after each rollout
        objectiveFn = self.settings["objective"]
        score, state = objectiveFn() # call app method
        # remember best plan
        if score > self.bestPlanScore:
            self.bestPlanScore = score
            self.bestPlanNode = self.currentNode
            self.bestPlanState = copy.copy(state)
        return score

    def setNodeChoices(self, node, choices):
        with self.sharedNodesLock:
            self.logMsg("setNodeChoices() node " + str(node.id) + " set choices: " + str(choices))
            node.unexploredChoices = copy.copy(choices)  # TODO: is this copy required?
            node.status = "open"
            self.updateSharedNode(node)
            node = self.getNode(node.id)
            self.logMsg("setNodeChoices() result: "+str(node))

    def updateNodeScore(self, score):
        # called by application code
        if self.currentNode:
            self.currentNode.score = score

    def updateTree(self, child, score):
        # Backpropagate rollout rewards and update MCTS stats
        # Climb up tree from child through ancestors to root
        # self.logMsg("updateTree() child: "+str(child))
        with self.sharedNodesLock:
            child.totalReward += self.roundIt(score)
            child.visitCount += 1
            child.avgReward = child.totalReward / child.visitCount
            if self.useSharedNodes:
                self.updateSharedNode(child)
            while child.parent:
                parent = self.getNode(child.parent)
                parent.visitCount += 1
                parent.totalReward += score
                parent.avgReward = parent.totalReward / parent.visitCount
                if self.useSharedNodes:
                    self.updateSharedNode(parent)
                child = parent

    def collectReplayPlan(self, node):
        # collects plan in reverse order so pop() can be used
        self.logMsg("collectReplayPlan() node: "+str(node))
        with self.sharedNodesLock:
            self.replayPlan = []
            self.collectReplayPlanRecursive(self.getNode(node.id))
        self.logMsg("collectReplayPlan() result: "+str(list(reversed(self.replayPlan))))

    def collectReplayPlanRecursive(self, node):
        # RECURSIVE
        # collect all plan choices from root to node (SCRs)
        if node.priorMove:
            self.replayPlan.append(node.priorMove)
            self.collectReplayPlanRecursive(self.getNode(node.parent))

    def collectParallelResults(self, parallelResults):
        treeResults = {"bestScore": self.bestPlanScore, "bestState": self.bestPlanState,"randomPct": self.randomChoicePct}
        moves = []
        for childId in self.root.children:
            child = self.getNode(childId)
            result = {"move": child.priorMove, "avgReward": child.avgReward, "visits": child.visitCount}
            moves.append(result)
        treeResults["moves"] = moves
        parallelResults.append(treeResults)

    def getMostPlayedNextMove(self):
        self.logMsg("getMostPlayedNextMove()")
        winner = None
        for childId in self.root.children:
            child = self.getNode(childId)
            self.logMsg("root child: "+str(child))
            if not winner:
                winner = child
            elif child.visitCount > winner.visitCount:
                winner = child
            elif child.visitCount == winner.visitCount and child.avgReward > winner.avgReward:
                # tie-breaker
                winner = child
        self.mostPlayedMove = winner
        print("\nMost Played root child: "+str(self.mostPlayedMove))
        self.printNodePlan(self.mostPlayedMove)
        # self.printTree()
        return winner

    def getNextReplayMove(self):
        # pop removes last item in list
        return self.replayPlan.pop()

    def createRootNode(self):
        self.logMsg("createRootNode()")
        self.root = self.createNode()
        self.root.depth = 1

    def createNode(self):
        with self.sharedNodesLock:
            node = MctsNode(self.getNextNodeId())
            node.unexploredChoices = None # set by chooseValue()
            self.openNodes.append(node)
            self.allNodes[node.id] = node
            if self.useSharedNodes:
                self.sharedNodes.append(node)
            return node


    def createChildNode(self, parent, name, cmdChoice):
        with self.sharedNodesLock:
            child = self.createNode()
            child.name = name
            child.parent = parent.id
            parent.children.append(child.id)
            child.depth = parent.depth + 1
            child.unexploredChoices = None # determined at next choice point
            # mark the edge (cmdChoice) from parent
            child.priorMove = (cmdChoice)
            self.logMsg("createChildNode() child: "+str(child))
            if self.useSharedNodes:
                self.updateSharedNode(child)
                self.updateSharedNode(parent)
            return child

    def getNode(self, nodeId):
        if self.useSharedNodes:
            with self.sharedNodesLock:
                node = self.sharedNodes[nodeId-1]
        else:
            node = self.allNodes[nodeId] if nodeId in self.allNodes else None
        return node

    def getNextNodeId(self):
        if self.useSharedNodes:
            with self.sharedNodesLock:
                return len(self.sharedNodes)+1
        else:
            self.nextNodeId += 1
            return self.nextNodeId

    def updateSharedNode(self, node):
        if self.useSharedNodes:
            with self.sharedNodesLock:
                id = node.id
                priorNode = self.getNode(id)
                priorNodeId = priorNode.id
                # self.logMsg("updateSharedNode() id: "+str(id) +", priorNodeId: "+str(priorNodeId))
                assert id == priorNodeId, "updateSharedNode() ERROR! id mismatch id: "+str(id)+", priorId: "+str(priorNodeId)
                self.sharedNodes[id-1] = node


    def setCurrentNode(self, node):
        self.logMsg("setCurrentNode() "+str(node))
        self.currentNode = node

    def getCurrentNode(self):
        return self.currentNode

    def printParallelResults(self):
        self.logMsg("Best Results ("+str(len(self.parallelResults))+"):")
        # TODO: sort results in decending objective order
        for treeResult in self.parallelResults:
            bestScore = treeResult["bestScore"]
            moves = treeResult["moves"] if "moves" in treeResult else []
            randomPct = treeResult["randomPct"]
            fullMsg = "Best score: "+str(bestScore)
            if bestScore == self.bestPlanScore:
                fullMsg += "*"
            for dict in moves:
                move = dict["move"]
                avgRwd = round(dict["avgReward"],3)
                visits = dict["visits"]
                fullMsg += ", "+ move+": "+str(avgRwd)+ " pts/"+str(visits)+" visits"
            fullMsg +=", random %: "+str(randomPct)
            self.logMsg(fullMsg)

    def printPathFromRoot(self, node):
        path = [node]
        while node.parent:
            node = self.getNode(node.parent)
            path.append(node)
        self.logMsg("printPathFromRoot()")
        for n in path:
            self.logMsg(str(n))

    def printTree(self):
        with self.sharedNodesLock:
            root = self.sharedNodes[0] if self.sharedNodes else self.root
            print("\n-----------\n"+str(self.printTreeRecursive(root, 0))+"\n")

    def printTreeRecursive(self, node, level):
        # RECURSIVE
        if node is None:
            # find root
            node = self.root
        if node:
            msg = "\n"
            if level == 0:
                msg = "[root]\n"
                level += 1
            for i in range(level):
                msg += "  "
            msg += str(node)
            for childId in node.children:
                child = self.getNode(childId)
                # RECURSIVE !!!
                msg += self.printTreeRecursive(child, level + 1)
            return msg
        else:
            print("printTree() *** ERROR *** root not found!")

    # def printSharedTree(self):
    #     root = self.sharedNodes[0]
    #     print("\n-----------\n"+str(self.printSharedTreeRecursive(root, 0))+"\n")
    #
    # def printTreeRecursive(self, node, level):
    #     # RECURSIVE
    #     if node is None:
    #         # find root
    #         node = self.root
    #     if node:
    #         msg = "\n"
    #         if level == 0:
    #             msg = "[root]\n"
    #             level += 1
    #         for i in range(level):
    #             msg += "  "
    #         msg += str(node)
    #         for childId in node.children:
    #             child = self.getNode(childId)
    #             # RECURSIVE !!!
    #             msg += self.printTreeRecursive(child, level + 1)
    #         return msg
    #     else:
    #         print("printTree() *** ERROR *** root not found!")

    def printNodePlan(self, node):
        path = [node]
        while node.parent:
            parent = self.getNode(node.parent)
            path.append(parent)
            node = parent
        path.reverse()
        print("\n------------\nNode Plan for "+str(node))
        for node in path:
            print(str(node))

    def setStage(self, stage):
        self.stage = stage
        self.logMsg("Stage = "+self.stage)

    def initStats(self):
        self.stats["startTime"] = time.localtime()
        self.stats["startTimestamp"] = time.strftime("%H:%M:%S", self.stats["startTime"])
        self.stats["timerStart"] = time.time()


    def stopRolloutStats(self, rolloutStart, rolloutNumber):
        elapsedMinutes, elapsedSecs = divmod(time.time() - rolloutStart, 60)
        elapsedHours, elapsedMinutes = divmod(elapsedMinutes, 60)
        elapsedString = ""
        if elapsedHours:
            elapsedString += str(elapsedHours)+ " h, "
        if elapsedMinutes:
            elapsedString += str(int(elapsedMinutes)) + " m, "
        elapsedString += format(elapsedSecs, '.3f')+" s"
        self.logMsg("Rollout duration: "+elapsedString)
        self.rolloutStats[rolloutNumber] = {"time": elapsedString}

    def printStats(self):
        self.stats["timerEnd"] = time.time()
        self.stats["endTime"] = time.localtime()
        self.stats["endTimestamp"] = time.strftime("%H:%M:%S", self.stats["endTime"])
        elapsedMinutes, elapsedSecs = divmod(self.stats["timerEnd"] - self.stats["timerStart"], 60)
        elapsedHours, elapsedMinutes = divmod(elapsedMinutes, 60)
        elapsedString = ""
        if elapsedHours:
            elapsedString += str(elapsedHours)+ " h, "
        if elapsedMinutes:
            elapsedString += str(int(elapsedMinutes)) + " m, "
        elapsedString += format(elapsedSecs, '.3f')+" s"
        self.stats["elapsed"] = elapsedString
        print("\nTime: "+self.stats["startTimestamp"]+"-"+self.stats["endTimestamp"]+", elapsed: "+elapsedString)
        print("loop limit: "+str(self.rolloutLimit))

    def collectMemoryStats(self):
        # NOT USED
        ramUsed = str(round(psutil.virtual_memory()[3]/1000000000, 3))+" GB"
        currentMem, peakMem = tracemalloc.get_traced_memory()
        currentMem = round(currentMem/(1024*1024), 3) # MB

    def writeDebugFiles(self, filepath):
        # filename = filepath + "/searchTree.txt"
        # print("Writing debug file: MCTS search tree")
        # treeString = self.printTreeRecursive(self.root, 0)
        # with open(filename, "w") as f:
        #     f.write(time.strftime("%m/%d/%Y %H:%M:%S", currentTime)+"\n\nMCTS Search Tree\n")
        #     f.write("\n"+str(treeString))
        currentTime = time.localtime()
        filename = "/rolloutStats.txt"
        filename = filepath + filename
        print("Writing debug file: rollout stats")
        with open(filename, "w") as f:
            f.write(time.strftime("%m/%d/%Y %H:%M:%S", currentTime)+"\n\n"+str(self.rolloutLimit)+ " rollouts\n\n")
            rolloutKeys = sorted(list(self.rolloutStats.keys()))
            for rollout in rolloutKeys:
                rolloutDuration = self.rolloutStats[rollout]["time"]
                msg = str(rollout)+": "+rolloutDuration
                msg += "\n"
                f.write(msg)

    def roundIt(self, n, precision=3):
        return round(n, precision)

    def timestamp(self):
        dt = datetime.now()
        timestamp = dt.strftime("%m/%d/%y %H:%M:%S")
        return timestamp

    def loggerMsgHandler(self, loggerQ):
        print("loggerMsgHandler() started")
        done = False
        priorThread = ""
        while not done:
            msg = loggerQ.get()  # blocks
            if msg == "LOGGER_EXIT":
                done = True
            thread = msg[:3]
            if len(msg) > 0 and not thread == priorThread:
                priorThread = thread
                msg = "\n"+msg
            print(msg)
        print("loggerMsgHandler() exit")


    def logMessage(self, msg):
        with self.logLock:
            self.loggerQ.put(msg)

    def logMsg(self, msg, includeBlankLine = False):
        # if not self.loggingEnabled:
        #     return
        sup = self.planner
        if sup:
            sup.logMsg(msg, includeBlankLine)
        else:
            pid = os.getpid()
            if not sup and pid == self.execPid:
                msg = "executive/"+self.prettyPid(pid)+": "+msg
            # if includeBlankLine:
            #     msg = "\n"+msg
            # msg = "["+msg+"]"
            self.logMessage(msg)
            # with self.logLock:
            #     print(msg)

    def prettyPid(self, pid):
        return str(pid - self.pid)

