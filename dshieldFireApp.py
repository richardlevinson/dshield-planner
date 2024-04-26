import copy
import math
import time
import matplotlib.pyplot as plt

from collections import OrderedDict

from dshieldPlanner import DshieldPlanner
from fileUtil import *

import multiprocessing as mp

class DshieldFireApp:
    def __init__(self):
    
        # Config parameters
        self.dataPathRoot = "/Users/richardlevinson/dshieldFireData/"
        self.experiment = "expt2"
        self.experimentRun = "RUN001"
        self.planHorizonStart = 0
        self.planHorizonDuration = 24 * 3600 # seconds
        # self.satList = ["CYG41884"]
        self.satList = ["CYG41884", "CYG41885", "CYG41886", "CYG41887"]#, "CYG41888"]#, "CYG41889", "CYG41890", "CYG41891"]
        self.powerModelName = "model1"
        self.storageParams = {"capacity": 5772, "collectionRatePerSec": 96.2172, "downlinkRatePerSec": 4} # megabits
        self.plannerParams = {"objective": self.updatePlanScore,  "rolloutLimit": 40000, "processCount": 10, "greedy": False, "allGreedy": False, "planHorizon": str(self.planHorizonDuration/3600)+" hrs"}

        # Internal initialization

        # constants
        self.experimentDataPath = self.dataPathRoot + self.experiment+"/"
        self.plannerFilepath = self.experimentDataPath + "planner/"
        self.energyMax = None # Joules, set by initPowerModel()
        self.energyMin = None # Joules, set by initPowerModel()
        self.greedy = self.plannerParams["greedy"]
        self.allGreedy = self.plannerParams["allGreedy"]

        self.satChoices = {}  #{sat: {tp: {sourceId: [gpList]}}}
        self.targetValues = {}
        self.eclipses = {}
        self.powerModel = None
        self.allPlanVars = {}  # includes every second (for logging only)
        self.initialPlanVars = [] # created once, filtered to remove all vars with a single choice (IDL or ***)
        self.planVars = {} # copied from initialPlanVars on each rollout
        self.planVarKeysSorted = []
        # self.planVarTerms = {}
        self.gpVars = {} # Maps each GP to the variables with cmd choices which cover the GP
        self.state = {} # dynamically updated by updateState()

        self.planner = DshieldPlanner(self.plannerParams)
        self.bestPlan = {}
        self.fileMgr = FileUtil(self)

    def run(self):
        print("\nDshieldFirePlanner.run() satellites: "+str(len(self.satList)))
        print("   data storage model: "+str(self.storageParams))
        self.fileMgr.readInputs()
        self.initPowerModel()
        self.createPlanVars()

        # init planner
        self.planner.start(self.createConstellationPlan)
        self.extractBestPlan()
        self.fileMgr.writeResultFiles()
        self.simulateAndVerifyPlan()
        for sat in self.satList:
            satState = self.getSatState(sat)
            self.fileMgr.writeImageInfo(sat, satState["images"])
        print("Fire Planner Done")

    def createConstellationPlan(self):
        # Top-level application code, simulated on each MCTS rollout
        self.planner.logMsg("createConstellationPlan()")
        self.initializeState()
        self.initializePlanVars()
        while self.planVarKeysSorted:
            varName = self.planVarKeysSorted[0]
            varChoices = self.popPlanVar(varName)
            choiceDict = {"varName": varName, "choices": varChoices}
            choiceDict = self.forceDownlinkIfStorageNotEmpty(choiceDict)
            # call MCTS for choice point
            # TODO: why do we pass varname to chooseValue?
            if self.greedy or self.allGreedy:
                cmd = self.planner.chooseValue(choiceDict, self.sortChoicesByCmdScore)  # TODO: ps sat or varName to localHeuristic
            else:
                cmd = self.planner.chooseValue(choiceDict, "random")     #TODO: ps sat or varName to localHeuristic

            self.updateState(varName, cmd)
            self.propagateChoice(varName, cmd)
        self.planner.logMsg("createConstellationPlan() done")

    def initializePlanVars(self):
        # called on each rollout
        self.planVarKeysSorted = []
        varPairs = []
        for varName, choices in self.initialPlanVars:
            self.planVars[varName] = choices
            _, tp = varName.split(".")
            varPairs.append((varName, int(tp)))
        sortedPairs = sorted(varPairs, key=lambda x: x[1])
        for v in sortedPairs:
            self.planVarKeysSorted.append(v[0])

        self.removeInitialInfeasibleChoices() # remove invalid choices (after populating planVarKeysSorted)

        # initialize planVarTerms and gpVars if necessary (first time initializePlanVars is called only)
        # if not self.planVarTerms and not self.gpVars:
        if not self.gpVars:
            for varName in self.planVarKeysSorted:
                # sat, tick = varName.split(".")
                # self.planVarTerms[varName] = (sat, int(tick))
                cmd, choices = self.planVars[varName]
                if cmd.startswith("RAW"):
                    cmdName, gpList = cmd.split(".")
                    gpList = [int(x) for x in gpList.split(",")]
                    for gp in gpList:
                        if gp in self.gpVars:
                            self.gpVars[gp].append(varName)
                        else:
                            self.gpVars[gp] = [varName]

    def forceDownlinkIfStorageNotEmpty(self, choiceDict):
        sat,tick = choiceDict["varName"].split(".")
        if self.isStorageEmpty(sat):
            return choiceDict
        varChoices = choiceDict["choices"]
        isDownlinkOpportunity = False
        for choice in varChoices:
            if choice.startswith("DNL."):
                isDownlinkOpportunity = True
                break
        if isDownlinkOpportunity:
            assert 'IDL' in varChoices, "forceDownlinkIfStorageNotEmpty() ERROR! IDLE missing from var choices: "+str(choiceDict)
            filteredChoices = copy.copy(varChoices)
            filteredChoices.remove('IDL')
            choiceDict["choices"] = filteredChoices
        return choiceDict


    def propagateChoice(self, varName, cmd):
        sat, tick = varName.split(".")
        # TODO: handle low power case
        # if self.isLowPower():
        #   self.removeAllChoices() # remove choices until power > min
        self.removeInfeasibleChoices(sat, cmd)
        self.removeObservedGpFromChoices(cmd, varName)

    def removeInfeasibleChoices(self, sat, cmd):
        # assumes planVarKeysSorted has already been created
        if cmd.startswith("RAW") and self.isStorageFull(sat):
            self.removeObservationChoices(sat)
        elif cmd.startswith("DNL") and self.isStorageEmpty(sat):
            self.removeDownlinkChoices(sat)


    def removeInitialInfeasibleChoices(self):
        # assumes planVarKeysSorted has already been created
        for sat in self.satList:
            if self.isStorageFull(sat):
                self.removeObservationChoices(sat)
            elif self.isStorageEmpty(sat):
                self.removeDownlinkChoices(sat)

    def removeObservationChoices(self, sat):
        # Removes observation choices until next DNL opportunity, because storage is full
        # Removes variables with less than 2 choices
        # Assumes storage is full (checked by caller)
        isDownlinkAvailable = False
        varsToRemove = []
        for varName in self.planVarKeysSorted:
            if varName.startswith(sat):
                choices = self.planVars[varName]
                filteredChoices = []
                for choice in choices:
                    if choice.startswith("DNL"):
                        isDownlinkAvailable = True
                    if isDownlinkAvailable or not choice.startswith("RAW"):
                        filteredChoices.append(choice)
                if len(filteredChoices) > 1:
                    self.planVars[varName] = filteredChoices
                else:
                    if filteredChoices[0] == 'IDL':
                        varsToRemove.append(varName)
                    else:
                        print("removeObservationChoices() ERROR! Removing non idle choice: "+str(varName)+", choice: "+str(filteredChoices))
                if isDownlinkAvailable:
                    break # exit loop for varName in self.planVars.keys()

        for varName in varsToRemove:
            self.popPlanVar(varName)

    def removeDownlinkChoices(self, sat):
        # Removes downlink choices until next observation opportunity, because storage is empty
        # Removes variables with less than 2 choices
        # Assumes storage is empty (checked by caller)
        isTargetAvailable = False
        varsToRemove = []
        for varName in self.planVarKeysSorted: #self.planVars.keys():
            if varName.startswith(sat):
                choices = self.planVars[varName]
                filteredChoices = []
                for choice in choices:
                    if choice.startswith("RAW"):
                        isTargetAvailable = True
                    if isTargetAvailable or not choice.startswith("DNL"):
                        filteredChoices.append(choice)
                if len(filteredChoices) > 1:
                    self.planVars[varName] = filteredChoices
                else:
                    if filteredChoices[0] == 'IDL':
                        varsToRemove.append(varName)
                    else:
                        print("removeDownlinkChoices() ERROR! Removing non idle choice: "+str(varName)+", choice: "+str(filteredChoices))
                if isTargetAvailable:
                    break # exit loop for varName in self.planVars.keys()
        for varName in varsToRemove:
            self.popPlanVar(varName)


    def removeObservedGpFromChoices(self, cmd, varName):
        if cmd.startswith("RAW"):
            cmdName, params = cmd.split(".")
            observedGpText = params.split(",")
            observedGpList = [int(gp) for gp in observedGpText]
            varsToRemove = []
            for gp in observedGpList:
                vars = self.gpVars[gp]
                for otherVarName in vars:
                    if otherVarName != varName and otherVarName in self.planVars:
                        varChoices = self.planVars[otherVarName]
                        newChoices = []
                        for otherCmd in varChoices:
                            newCmd = None
                            if otherCmd.startswith("RAW"):
                                newCmd = self.stripObservedGps(otherCmd, observedGpText)
                            else:
                                newCmd = otherCmd
                            if newCmd:
                                newChoices.append(newCmd)
                        # replace planVar's choices
                        if len(newChoices) > 1:
                            self.planVars[otherVarName] = newChoices  #destructive change in self.planVars
                        else:
                            # remove vars with less than two choices
                            if otherVarName not in varsToRemove:
                                varsToRemove.append(otherVarName)
            # end for gp in observedGpList
            if varsToRemove:
                # print("removing "+str(len(planVarsToRemove))+ " vars")
                for v in varsToRemove:
                    self.popPlanVar(v)

    def stripObservedGps(self, cmd, observedGps):
        # strip observedGPs from cmd
        # return no gps remain then return None
        newCmd = cmd
        for g in observedGps:
            if g in cmd:
                newCmd = newCmd.replace(g, "").replace(".,",".").replace(",,",",")
        if newCmd.endswith(","):
            newCmd = newCmd[:-1]
        if not newCmd.endswith("."):
            return newCmd
        else:
            return None

    def readPlanVarsFromFile(self):
        self.initialPlanVars = self.fileMgr.readPlanVarsFile()

    def readPlanVars(self):
        print("readPlanVars()")
        path = self.experimentDataPath +"planner/planChoicesSmall.txt"
        assert os.path.exists(path), "readPlanVars() ERROR! path not found: "+path
        vars = []
        with open(path, "r") as f:
            dictIn = ""
            for line in f:
                if line.startswith("("):
                    var, domain = ast.literal_eval(line)
                    vars.append((var, domain))
                    self.allPlanVars[var] = domain
        print("readPlanVars() var count: "+str(len(vars)))
        return vars

    def createPlanVars(self):
        print("createPlanVars()")
        # self.initialPlanVars = self.readPlanVars()
        # return
        obsVarCount = 0
        dnlVarCount = 0
        for sat in self.satList:
            choices = self.satChoices[sat]
            tpList = sorted(choices.keys())
            for tp in tpList:
                if tp > self.planHorizonStart + self.planHorizonDuration:
                    break
                varName = sat + "."+str(tp)
                varDomain = choices[tp]
                if list(varDomain.keys())[0] == "GAP":
                    varDomain = "***"
                elif "DNL" in varDomain:
                    varDomain = "DNL."+str(varDomain["DNL"])
                    varDomain = varDomain
                    dnlVarCount += 1
                else:
                    gpList = []
                    for sourceId in varDomain.keys():
                        gpList.extend(varDomain[sourceId])
                    gpList = str(sorted(gpList)).replace(" ","").strip("[,]")
                    varDomain = "RAW." + gpList
                    obsVarCount += 1
                varDomain = [varDomain]
                if "***" not in varDomain:
                    varDomain.append("IDL")
                self.allPlanVars[varName] = varDomain
                if len(varDomain) > 1:
                    self.initialPlanVars.append((varName, varDomain))
        self.fileMgr.writePlanVarFile(False) # all vars
        self.fileMgr.writePlanVarFile(True)  # filtered to remove vars with only a single choice (IDLE)
        print("createPlanVars() created "+str(len(self.initialPlanVars))+" vars")
        print("obsVarCount: "+str(obsVarCount)+", dnlVarCount: "+str(dnlVarCount))

    def popPlanVar(self, varName):
        # print("popPlanVar() "+varName)
        assert varName in self.planVars, "popPlanVar() varName "+varName +  " not in planVars"
        assert varName in self.planVarKeysSorted, "popPlanVar() varName "+varName +  " not in planVarKeysSorted"
        poppedVarChoices = self.planVars.pop(varName)
        self.planVarKeysSorted.remove(varName)
        assert len(self.planVars) == len(self.planVarKeysSorted), "popPlanVar() mismatch! planVars : "+str(len(self.planVars))+ ", varKeys: "+str(len(self.planVarKeysSorted))
        return poppedVarChoices

# STATE MANAGEMENT METHODS

    def initializeState(self):
        # observedGP = orderedDict {GP : [targetValue, % downlinked], }
        # dynamic state
        for sat in self.satList:
            self.state[sat] = {"storageUsed": 0, "energy": self.initialEnergy, "observedGP": OrderedDict(), "images": OrderedDict(), "plan": []}

    def getSatState(self, sat):
        return self.state[sat]

    def updateState(self, varName, cmd):
        # called by createConstellationPlan()
        sat, tick = varName.split(".")
        satState = self.getSatState(sat)
        if cmd.startswith("RAW"):
            self.incrementStorage(satState)
            self.updateImages(cmd, satState)
        elif cmd.startswith("DNL"):
            self.decrementStorage(satState)
            self.updateDownlinkedImagePct(satState)
        self.updateEnergyState(sat, tick, cmd)
        satState["plan"].append((varName, cmd))

    def updateStateForVerification(self, sat,planStep, priorStep):
        # called by createConstellationPlan()
        satState = self.getSatState(sat)
        cmd = planStep["cmd"]
        if cmd.startswith("RAW"):
            self.incrementStorage(satState)
            self.updateImagesForVerification(satState, planStep)
        elif cmd.startswith("DNL"):
            self.decrementStorage(satState)
            self.updateDownlinkedImagePct(satState, planStep["tick"])
        self.updateEnergyStateForVerification(planStep, priorStep)


    def incrementStorage(self, satState):
        satState["storageUsed"] += self.storageParams["collectionRatePerSec"]
        satState["storageUsed"] = round(satState["storageUsed"], 3)
        # print("incrementStorage() storageUsed: "+str(self.state["storageUsed"]))
        assert satState["storageUsed"] <= self.storageParams["capacity"], "incrementStorage() ERROR! negative storageUsed! "+str(satState["storageUsed"])

    def decrementStorage(self, satState):
        satState["storageUsed"] -= self.storageParams["downlinkRatePerSec"]
        satState["storageUsed"] = round(max(0, satState["storageUsed"]), 3)
        # print("decrementStorage() storageUsed: "+str(self.state["storageUsed"]))
        assert satState["storageUsed"] >= 0, "decrementStorage() ERROR! negative storageUsed! "+str(satState["storageUsed"])

    def isStorageFull(self, sat):
        if self.getStorageState(sat) > self.storageParams["capacity"] - self.storageParams["collectionRatePerSec"]:
            return True
        else:
            return False

    def isStorageEmpty(self, sat):
        return True if self.getStorageState(sat) <= 0 else False

    def getStorageState(self, sat):
        satState = self.getSatState(sat)
        result = satState["storageUsed"]
        return result

    def updateImages(self, cmd, satState):
        cmdTerms = cmd.split(".")
        params = cmdTerms[1].split(".")
        gpList = params[0].split(",")
        gpIntList = [int(gp) for gp in gpList]
        self.extendImagesDict(satState, gpIntList)

    def updateImagesForVerification(self, satState, planStep):
        tick = planStep["tick"]
        targets = planStep["targets"]
        self.extendImagesDict(satState, targets, tick)

    def extendImagesDict(self, satState, newObservedGP, tick=None):
        # imagesDict = {imageID: [sum(values of newObservedGP), % downlinked]}
        imageValue = round(sum([self.targetValues[gp] for gp in newObservedGP]), 5)
        imageId = len(satState["images"])+1 # +1 so that image ID 0 is not mistaken for null
        imageInfo = {"value": imageValue, "downlinkPct": 0.00, "targets": newObservedGP}
        if tick:
            imageInfo.update({"start": tick}) # used for tracking latency (post-processing only)
        satState["images"][imageId] = imageInfo # [value, % downlinked]

    def updateDownlinkedImagePct(self, satState, tick=None):
        # used for downlink score
        # TODO: handle case when downlink spans 2 images
        downlinkImage = self.getCurrentDownlinkImage(satState)
        if downlinkImage:
            imageInfo = satState["images"][downlinkImage]
            observationDownlinkPctPerSec = round(self.storageParams["downlinkRatePerSec"] /  self.storageParams["collectionRatePerSec"], 3)
            newPct = imageInfo["downlinkPct"] + observationDownlinkPctPerSec
            if newPct < 1:
                imageInfo["downlinkPct"] = newPct
            else:
                # downlink spans 2 images
                imageInfo["downlinkPct"] = 1.0  # top of the first image
                # start downloading second image
                overflow = round(newPct - 1,5)
                nextImage = self.getCurrentDownlinkImage(satState)
                if nextImage:
                    nextImageInfo = satState["images"][nextImage]
                    nextImageInfo["downlinkPct"] = overflow
                    nextImageInfo["downlinkPct"] = round(nextImageInfo["downlinkPct"], 3)
            imageInfo["downlinkPct"] = round(imageInfo["downlinkPct"], 3)
            if tick and imageInfo["downlinkPct"] == 1:
                latency = tick - imageInfo["start"]
                imageInfo.update({"end": tick, "latency": latency})

    def getCurrentDownlinkImage(self, satState):
        for image in satState["images"]:
            imageInfo = satState["images"][image]
            if imageInfo["downlinkPct"] < 1.0:
                return image

    def collectObservedTargets(self, images):
        # UNUSED
        print("collectObservedTargets() imageCount: "+str(len(images)))
        targets = []
        for image in images:
            targetValue, downlinkPct, gpList = images[image]
            targets.extend(gpList)
        print("collectObservedTargets() imageCount: "+str(len(images)) + ", observed: "+str(len(targets))+", set: "+str(len(set(targets))))
        return targets


# POWER MODEL

    def initPowerModel(self):
        # power model constants
        self.energyMax = self.powerModel["maxCharge"] * 3600 # Joules
        self.energyMin     = self.energyMax * (self.powerModel["minChargePct"]/100)     # Joules
        self.initialEnergy = self.energyMax * (self.powerModel["initialChargePct"]/100) # Joules
        print("\ninitPowerModel() model: "+str(self.powerModel) +" initial: "+str(self.initialEnergy)+", min: "+str(self.energyMin)+", max: "+str(self.energyMax)+"\n")

    def updateEnergyState(self, sat, tick, cmd):
        tick = int(tick)
        priorTick = self.getPriorTimestepForSat(sat, tick)
        self.updateEnergyStateDetails(sat, tick, cmd, priorTick)

    def updateEnergyStateForVerification(self, planStep, priorStep):
        sat = planStep["sat"]
        tick = planStep["tick"]
        cmd = planStep["cmd"]
        priorTick = priorStep["tick"] if priorStep else -1
        self.updateEnergyStateDetails(sat, tick, cmd, priorTick)

    def updateEnergyStateDetails(self, sat, tick, cmd, priorTick):
        # calculate energy level at the end of tick (after executing cmd)
        # energy values are in Joules
        satState = self.getSatState(sat)
        initialEnergy = satState["energy"]
        # add energyIn since priorTick
        energyIn = 0
        energyOut = None
        for t in range(priorTick+1, tick+1):
            if not self.isSatInEclipse(sat, t):
                if initialEnergy + energyIn < self.energyMax:
                    energyIn += self.powerModel["powerIn"]  # power is Watts  = Jules/second
            # Sensor is always on so add its consumption to the idle power consumption
            energyOut = self.powerModel["idlePowerOut"] + self.powerModel["sensorPowerOut"] # 1 second of power
            if cmd.startswith("DNL"):
                energyOut += self.powerModel["downlinkPowerOut"] # 1 second of power
        energyLevel = min(initialEnergy + energyIn, self.energyMax)  # never exceed energyMax
        if energyOut:
            energyLevel -= energyOut
        else:
            print("updateEnergyStateDetails() ERROR no energyOut! tick: "+str(tick)+", priorTick: "+str(priorTick))
        satState["energy"] = energyLevel
        # self.printEnergyDebuggingMsg(varName, initialEnergy, energyIn, energyOut, energyLevel)

    def isSatInEclipse(self, satId, tick):
        if satId in self.eclipses:
            if tick in self.eclipses[satId]:
                return True
            else:
                return False
        else:
            print("isSatInEclipse() ERROR! satId "+str(satId) +" not found")
        return False

    def getPriorTimestepForSat(self, sat, tick):
        # TODO: do we need to check if otherVarSat == sat?
        satState = self.getSatState(sat)
        plan = satState["plan"]
        index = -1
        while abs(index) <= len(plan):
            otherVar, otherCmd = plan[index]
            otherVarSat, otherVarTick = otherVar.split(".")
            otherVarTick = int(otherVarTick)
            # otherVarSat, otherVarTick = self.planVarTerms[otherVar]
            # otherVarTick = otherVarTick
            if otherVarSat == sat and otherVarTick < tick:
                return otherVarTick
            else:
                index -= 1
        return -1

    def printEnergyDebuggingMsg(self, varName, initialEnergy, energyIn, energyOut, energyLevel):
        chargePct = round((energyLevel/self.energyMax), 5) * 100
        msg = "updateEnergyState() var: "+varName+", initial: "+str(initialEnergy)+" + " + str(energyIn) + " - energyOut: "+ str(energyOut)+" = "+str(energyLevel)
        if energyLevel == self.energyMax:
            msg += "*"
        msg +=" "+str(chargePct)+" %"
        print(msg)


    # SCORE MANAGEMENT METHODS

    def updatePlanScore(self):
        # called by planner after rollout() and also during verification
        # returns state so planner can cache it for collecting best plan state at the end
        # collect half of targetScore when GP is observed, and the other half as GP is downloaded
        score = 0
        for sat in self.satList:
            satState = self.getSatState(sat)
            for image in satState["images"]:
                imageInfo = satState["images"][image]
                targetValue = imageInfo["value"]
                downlinkPct = imageInfo["downlinkPct"]
                observationValue = targetValue/2 # collect first half of reward at observation time
                downlinkValue = observationValue * downlinkPct
                score += (observationValue + downlinkValue)
        score = round(score, 3)
        return score, self.state

    def sortChoicesByCmdScore(self, choicesDict):
        # used by chooseValue()
        varName = choicesDict["varName"]
        choices = choicesDict["choices"]
        sat, tick = varName.split(".")
        choicePairs = []
        for choice in choices:
            cmdScore = self.getAggregateGpCmdScore(sat, choice)
            choicePairs.append((cmdScore, choice))
        sortedPairs = sorted(choicePairs, key=lambda c: c[0], reverse=True)  # sort by cmdScore (descending)
        sortedChoices = []
        for pair in sortedPairs:
            sortedChoices.append(pair[1])
        return sortedChoices

    def getAggregateGpCmdScore(self, sat, cmd):
        # local heuristic used by chooseValue()
        satState = self.getSatState(sat)
        images = list(satState["images"].keys())
        previouslyObservedGp = set()
        for image in images:
            gpList = satState["images"][image]["targets"]
            previouslyObservedGp.update(gpList)
        totalScore = 0
        if cmd.startswith("RAW"):
            cmd, params = cmd.split(".")
            gpList = params.split(",")
            gpList = [int(gp) for gp in gpList]
            for gp in gpList:
                # don't count duplicate observations
                if gp not in previouslyObservedGp:
                    observationScore = self.targetValues[gp]/2 # half of reward for observation
                    totalScore += observationScore
        elif cmd.startswith("DNL"):
            downlinkImage = self.getCurrentDownlinkImage(satState)
            if downlinkImage:
                imageInfo = satState["images"][downlinkImage]
                imageValue = imageInfo["value"]
                downlinkPct = imageInfo["downlinkPct"]
                observationScore = imageValue/2  # downlinkPct of observation reward for downlink
                totalScore = observationScore * downlinkPct
        return totalScore

    def pprintState(self, state):
        prettyPrint = True
        if prettyPrint:
            storage = state["storageUsed"]
            gpCount = len(state["observedGP"])
            msg = "storage: "+str(storage)+", gpCount: "+str(gpCount)
            if "score" in state:
                msg += ", score: "+str(state["score"])
        return msg

    # POST-PROCESSING UTILITIES
    def timestamp(self, t=None):
        if not t:
            t = time.localtime()
        return time.strftime("%H:%M:%S", t)

    def extractBestPlan(self):
        self.planner.logMsg("extractBestPlan()")
        bestPlanState = self.planner.bestPlanState
        satPlans = {}
        for sat in self.satList:
            satPlans[sat] = self.addMissingTimepoints(sat,bestPlanState[sat]["plan"])
        self.bestPlan = {"plan": satPlans, "node": self.planner.bestPlanNode, "state": bestPlanState, "score": self.planner.bestPlanScore}

    def addMissingTimepoints(self, sat, filteredPlan):
        # Re-Insert the timepoints which were filtered out because the only choice was IDL
        # Called by extractBestPlan
        fullPlan = []
        planDict = {}
        for planVar, planVarChoices in filteredPlan:
            planDict[planVar] = planVarChoices
        for varName in self.allPlanVars.keys():
            if varName.startswith(sat):
                if varName in planDict:
                    fullPlan.append((varName, planDict[varName]))
                else:
                    fullPlan.append((varName, "***"))
        return fullPlan

    def simulateAndVerifyPlan(self):
        # post-processings()
        self.initializeState()
        for sat in self.satList:
            satPlan = self.fileMgr.readBestPlanDetails(sat)
            print("\nSimulating best plan for sat "+sat +" ("+str(self.plannerParams["rolloutLimit"])+ " rollouts)")
            filepath = self.experimentDataPath + "planner/"+self.experimentRun
            filename = filepath + "/planSim."+sat+".txt"
            satState = self.getSatState(sat)
            minChargeStep = None
            minChargePct = None
            objectiveScore = 0
            gpCount = 0
            observedTargets = []
            with open(filename, "w") as f:
                f.write("Best plan for sat "+sat+ " ("+str(self.plannerParams["rolloutLimit"])+ " rollouts)\n\n")
                stepCount = 1
                priorStep = None
                for step in satPlan:
                    self.simulatePlanStep(sat, step, priorStep)
                    sat = step["sat"]
                    tick = step["tick"]
                    varName = sat+"."+str(tick)
                    cmd = step["cmd"]
                    if cmd.startswith("RAW"):
                        cmdMsg = "OBS"
                    else:
                        cmdMsg = cmd
                    priorStep = step
                    chargePct = round((satState["energy"]/self.energyMax) * 100, 2)
                    if not minChargePct or chargePct < minChargePct:
                        minChargePct = chargePct
                        minChargeStep = step
                    msg = "time: "+str(tick) + ", "+cmdMsg + ", bat. "+str(chargePct) +" %"
                    # msg = str(stepCount)+" "+varName + ": "+cmd
                    if cmd.startswith("OBS"):
                        msg += "+"
                    elif cmd.startswith("DNL"):
                        msg += "-"
                    if cmd not in ["IDL", "***"]:
                        msg += ", "+self.pprintState(satState)
                        objectiveScore = self.state["score"]
                        if cmd.startswith("RAW"):
                            observedTargets.extend(step["targets"])
                            gpCount += len(step["targets"])
                    if "targets" in step:
                        msg += ", targets: "+str(step["targets"])

                    # if self.isSatInEclipse(sat, self.getVarTick(varName)):
                    if self.isSatInEclipse(sat, tick):
                        msg += " (eclipse)"
                    f.write(msg+"\n")
                    if stepCount % 1000 == 0:
                        print("sim step: "+varName+": "+str(step["cmd"])+", "+self.timestamp(time.localtime()))
                    stepCount += 1
                # self.collectObservedTargets(self.bestPlan["state"]["images"])
                f.write("\nObjective: "+str(objectiveScore)+", GP observed: "+str(gpCount)+", Minimum bat. charge: "+str(minChargePct)+" % at time "+str(minChargeStep["tick"]))

    def simulatePlanStep(self, sat, step, priorStep):
        # called only for post-processing in simulateAndVerifyPlan()
        self.updateStateForVerification(sat, step, priorStep)
        score = self.updatePlanScore()
        self.state["score"] = score
        self.verifyState(sat, step)

    def verifyState(self, sat, step):
        # validate storage state
        satState = self.getSatState(sat)
        storageUsed = satState["storageUsed"]
        assert 0 <= storageUsed and storageUsed <= self.storageParams["capacity"], "validateState() ERROR! invalid storage level: "+str(storageUsed)+", planStep: "+str(step)

        # validate energy state
        energyLevel = satState["energy"]
        assert self.energyMin <= energyLevel and energyLevel <= self.energyMax, "validateState() ERROR! invalid energy level: "+str(energyLevel)+", planStep: "+str(step)

        # validate plan length
        # assert len(self.state["plan"]) <= len(self.initialPlanVars), "validateState() ERROR! too many plan steps: "+str(len(self.state["plan"]))+", planStep: "+str(step)

    def splitSatPlans(self, plan):
        # called only by simulateAndVerifyPlan()
        satPlans = {}
        for sat in self.satList:
            satPlans[sat] = []
        for step in plan:
            sat = step["sat"]
            satPlans[sat].append(step)
        return satPlans

def spin(i):
    print("spin "+str(i))
    terms = [x for x in range(25000)]
    for x in terms:
        for y in terms:
            z = x * y
    print("spin "+str(i) +" done")

def main():
    dshieldFireApp = DshieldFireApp()
    dshieldFireApp.run()

def mpTest():
    print("starting")
    xResults = []
    yResults = []
    yTicks = []
    for procCount in range(1, 50):
        startTime = time.time()
        print("procCount: "+str(procCount))
        procs = []
        for x in range(procCount):
            p = mp.Process(target=spin, args=(x,))
            p.start()
            procs.append(p)
        for p in procs:
            p.join()
        endTime = time.time()
        elapsed = round(endTime - startTime, 2)
        yTicks.append(math.ceil(elapsed))
        print("elapsed: " + str(elapsed))
        xResults.append(procCount)
        yResults.append(elapsed)
    for i in range(len(xResults)):
        print(str(xResults[i])+": "+str(yResults[i]))
    plt.plot(xResults, yResults)
    plt.xticks(xResults)
    # plt.yticks(yResults)
    plt.xlabel("# of procs")
    plt.ylabel("Total solve time all procs")
    plt.title("Total solve time vs. # of procs")
    plt.grid()
    plt.show()

if __name__ == '__main__':
    main()
    # mpTest()
