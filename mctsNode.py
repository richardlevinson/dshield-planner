import copy

class MctsNode:
    def __init__(self, nodeId):
        self.id = nodeId
        self.name = ""
        self.parent = None
        self.children = list()
        self.visitCount  = 0
        self.totalReward = 0  # total objective value over all iterations for this edge between parent and child
        self.avgReward = 0 # totalReward/visitCount
        self.status = "init"
        self.priorMove = None
        self.unexploredChoices = None
        self.planScore = None # set by application code
        self.depth = 0

    def hasChildren(self):
        return len(self.children) > 0

    def isLeaf(self):
        # True if node has unexplored choices (or choices have not been initialized)
        if self.status == "init":
            # allow new nodes to be selected before their choices are set
            return True
        return self.unexploredChoices and len(self.unexploredChoices) > 0

    def __str__(self):
        totalReward = str(round(self.totalReward, 3))
        avgReward = str(round(self.avgReward,3))
        msg = "["+str(self.id)
        if self.name:
            msg += ": "+str(self.name)
        msg +=", parent: "+str(self.parent)
        if self.priorMove:
            msg += ", move: "+str(self.priorMove)
        msg += ", unexploredChoices: "+str(self.unexploredChoices)
        if self.planScore:
            scoreMsg = str(self.planScore)
            msg += ", score: "+scoreMsg
        # msg +=", open choices: "+str(self.unexploredChoices)
        msg += ", avgReward: " +avgReward +" ("+totalReward+"/"+str(self.visitCount)+")"
        msg += ", depth: "+str(self.depth)+", "+self.status
        msg += "]"
        return msg
