# **** File operations *****
import ast
import copy
import os
import time

class FileUtil:

    def __init__(self, dshieldFirePlanner):
        self.planner = dshieldFirePlanner 

    def readInputs(self):
        print("readInputs()")
        self.readTargetValues()
        self.readPowerConfigFile()
        for sat in self.planner.satList:
            self.readSatChoiceFile(sat)
            self.readEclipseFileForSat(sat)
            choices = list(self.planner.satChoices[sat].keys())
            print("tp count for "+sat+": "+str(len(choices))+", TP range: "+str(choices[0]) +" - "+str(choices[-1]))
        print("target value count: "+str(len(self.planner.targetValues.keys())))

    def readSatChoiceFile(self, sat):
        satChoices = {} # {TP: {sourceID: [gpList]}}
        filepath = self.planner.plannerFilepath + self.planner.experimentRun+"/"
        filenames = os.listdir(filepath)
        filename = None
        for file in filenames:
            if file.startswith(sat+"_choices"):
                filename = file
                break
        filepath +=  filename

        print("readSatChoiceFile() reading file for "+sat+ ": "+filepath)
        with open(filepath, "r") as f:
            priorTp = 0
            for line in f:
                filteredLine = line.strip()
                if filteredLine and not filteredLine.startswith("--- GAP"):
                    dict = "{"+filteredLine+"}"
                    choices = ast.literal_eval(dict)
                    tp = list(choices.keys())[0]
                    if tp-1 != priorTp:
                        # insert TP for all gap seconds
                        for gapSecond in range (priorTp+1, tp):
                            satChoices.update({gapSecond: {"GAP": "***"}})
                    priorTp = tp
                    satChoices.update(choices)
                    # print("choices: "+str(choices))
        self.planner.satChoices[sat] = satChoices

    def readTargetValues(self):
        filepath = self.planner.plannerFilepath + self.planner.experimentRun+"/"
        filenames = os.listdir(filepath)
        filename = None
        for file in filenames:
            if file.startswith("TV_"):
                filename = file
                break
        filepath +=  filename

        print("readTargetValues() reading file: "+filepath)
        with open(filepath, "r") as f:
            firstLine = True
            for line in f:
                if firstLine:
                    firstLine = False
                    continue
                filteredLine = line.strip()
                if filteredLine:
                    gp, value = filteredLine.split(",")
                    gp = int(gp)
                    value = float(value)
                    self.planner.targetValues[gp] = value

    def readEclipseFileForSat(self, satId):
        print("readEclipseFilesForSat() sat: "+str(satId))
        if satId not in self.planner.eclipses:
            self.planner.eclipses[satId] = set()
        satEclipses = self.planner.eclipses[satId]

        path = self.planner.experimentDataPath + "operator/orbit_prediction/" + self.planner.experimentRun + "/" + satId + "/eclipse/"
        assert os.path.exists(path), "readEclipseFileForSat() ERROR! path not found: "+path
        eclipseFiles = [f for f in os.listdir(path) if "eclipse" in f]
        # TODO: Is there only one eclipseFile per sat?
        for file in eclipseFiles:
            filepath = path + file
            print("reading eclipse  file: "+filepath)
            with open(filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("start"):
                        if line.count(",") > 0:
                            terms = line.split(",")
                            start = int(terms[0])
                            end   = int(terms[1])
                            # satEclipses.append((start, end))
                            eclipse = [x for x in range(start, end+1)]
                            satEclipses.update(eclipse)

    def readPowerConfigFile(self):
        print("readPowerConfigFile()")
        path = self.planner.experimentDataPath +"planner/powerConfig.txt"
        assert os.path.exists(path), "readPowerConfigFile() ERROR! path not found: "+path
        with open(path, "r") as f:
            dictIn = ""
            for rawLine in f:
                line = rawLine.strip()
                if line and not line.startswith("#"):
                    dictIn += line
        dictIn = dictIn.strip()
        powerConfig = ast.literal_eval(dictIn)
        self.planner.powerModel = powerConfig["default"]
        if self.planner.powerModelName != "default":
            self.planner.powerModel.update(powerConfig[self.planner.powerModelName])

    def readBestPlanDetails(self, sat):
        filepath = self.planner.experimentDataPath + "planner/"+self.planner.experimentRun+"/bestPlan."+sat+".Details.txt"
        plan = []
        with open(filepath, "r") as f:
            skippingHeader = True
            for line in f:
                line = line.strip()
                if line:
                    if line.startswith("-----"):
                        skippingHeader = False
                    elif line.startswith("Downlinked"):
                        return plan
                    elif not skippingHeader:
                        varName, cmd = line.split(":")
                        varName = varName.strip()
                        sat, tick = varName.split(".")
                        planStep = {"sat": sat, "tick": int(tick)}
                        cmd = cmd.strip().replace("  ", " ")
                        if cmd.startswith("RAW") or cmd.startswith("DNL"):
                            cmdName, targets = cmd.split(" ")
                            if cmd.startswith("RAW"):
                                targets = [int(x) for x in targets.split(",")]
                            planStep.update({"cmd": cmdName, "targets": targets})
                        else:
                            planStep.update({"cmd": cmd})
                        plan.append(planStep)
        return plan


    def writePlanVarFile(self, filtered):
        filepath = self.planner.experimentDataPath + "planner/"+self.planner.experimentRun
        filename = filepath + "/planVars."
        if filtered:
            filename += "filtered."
        filename += "txt"
        vars = self.planner.initialPlanVars if filtered else self.planner.allPlanVars
        with open(filename, "w") as f:
            f.write("Var count: "+str(len(vars))+"\n\n")
            for var in vars:
                f.write(str(var)+"\n")

    def writeResultFiles(self):
        print("Writing result files")
        self.writeBestPlanFile(False) # concise
        self.writeBestPlanFile(True)  # verbose
        self.writeDebugFiles()

    def writeBestPlanFile(self, verbose):
        bestPlanNode = self.planner.bestPlan["node"]
        bestPlanState = self.planner.bestPlan["state"]
        totalObservedTargets = 0
        totalDownlinkedTargets = 0
        msg = "Writing best plan file "
        if verbose:
            msg += " (details)"
        else:
            msg += " (summary)"
        print(msg)
        score = round(self.planner.bestPlan["score"],3)
        print("\n** Best Plan Score: "+str(score))
        print("\nSearch Time: " + self.planner.planner.stats["startTimestamp"] + "-" + self.planner.planner.stats["endTimestamp"] + ", elapsed: " + self.planner.planner.stats["elapsed"])
        print("Rollout limit: " + str(self.planner.planner.rolloutLimit))
        print("\n\nBest Plan Node:\n"+str(bestPlanNode))
        filepath = self.planner.experimentDataPath + "planner/"+self.planner.experimentRun
        for sat in self.planner.satList:
            plan = self.planner.bestPlan["plan"][sat]
            filename = "/bestPlan."+sat+"."
            if verbose:
                filename += "Details"
            else:
                filename += "Summary"
            filename += ".txt"
            filename = filepath + filename
            with open(filename, "w") as f:
                priorCmd = None
                cmdStart = None
                lastVar = plan[-1][0]
                f.write(time.strftime("%m/%d/%Y %H:%M:%S", time.localtime())+"\n")
                f.write("Best Plan Score: "+str(score)+"\n")
                f.write("Rollout limit: " + str(self.planner.planner.rolloutLimit) + ", Search Time: " + self.planner.planner.stats["startTimestamp"] + "-" + self.planner.planner.stats["endTimestamp"] + ", elapsed: " + self.planner.planner.stats["elapsed"])
                f.write("\n\nBest Plan Node:\n"+str(bestPlanNode)+"\n")

                if verbose:
                    f.write("\n\nSatellite.TP:   command   \n")
                    f.write("-------------   ------- \n")
                else:
                    f.write("\n\n      Time slot:   command    (duration)\n")
                    f.write("  --------------   -------    ----------\n")
                for varName, choice in plan:
                    params = None
                    if "." in choice:
                        cmd, params = choice.split(".")
                    else:
                        cmd = choice
                    if verbose:
                        c = cmd
                        vname = varName+":"
                        msg = vname.ljust(16, " ")+str(c).ljust(5, " ")
                        if params:
                            msg += params
                        f.write(msg+"\n")
                    else:
                        terms = varName.split(".")
                        if not priorCmd:
                            cmdStart = int(terms[1])-1
                        elif cmd != priorCmd or varName == lastVar:
                            if priorCmd:
                                # if priorCmd == "RAW":
                                #     priorCmd += "+"
                                # elif priorCmd == "DNL":
                                #     priorCmd += "-"
                                cmdEnd = int(terms[1])-1
                                diffSecs = cmdEnd - cmdStart + 1
                                gapSize = str(diffSecs)+" s" if diffSecs < 60 else str(round(diffSecs/60, 2))+" m"
                                f.write(str(cmdStart).rjust(6, ' ')+ " - "+str(cmdEnd).rjust(6, ' ')+":     "+priorCmd.ljust(6, ' ')+"   ("+gapSize+")\n")
                                cmdStart = cmdEnd + 1
                        priorCmd = cmd

                # report downlinked GP info
                downlinkedTargets = []
                observedTargets = set()
                satImages = bestPlanState[sat]["images"]
                for image in satImages:
                    imageInfo = satImages[image]
                    downlinkPct = imageInfo["downlinkPct"]
                    observedTargets.update(imageInfo["targets"])
                    if downlinkPct > 0:
                        downlinkedTargets.append((image, round(downlinkPct,3)))
                # self.planner.collectObservedTargets(bestPlanState["images"])
                downlinkedTargetCount = len(downlinkedTargets)
                observedTargetCount = len(observedTargets)
                totalObservedTargets += observedTargetCount
                totalDownlinkedTargets += downlinkedTargetCount
                msg = "\n\nDownlinked Targets ("+str(downlinkedTargetCount)+"/"+str(observedTargetCount)+")\n"
                print(msg)
                f.write(msg)
                f.write(str(downlinkedTargets))

                if verbose:
                    # print observed GP
                    observedTargets = sorted(observedTargets)
                    f.write("\n\nGP targets ("+str(len(observedTargets))+"):\n")
                    f.write(str(observedTargets))
        # end for sat
        totalDownlinkedPct = round(totalDownlinkedTargets/totalObservedTargets,2)
        print("Target Totals: "+str(totalDownlinkedTargets)+"/"+str(totalObservedTargets)+ "  "+str(totalDownlinkedPct)+"%")

    def writeDebugFiles(self):
        filepath = self.planner.experimentDataPath + "planner/"+self.planner.experimentRun
        self.planner.planner.writeDebugFiles(filepath)

    def writeImageInfo(self, sat, images):
        filepath = self.planner.experimentDataPath + "planner/"+self.planner.experimentRun
        filename = filepath + "/"+sat+"."+"imageInfo.txt"
        with open(filename, "w") as f:
            for imageID in images:
                f.write(str(images[imageID])+"\n")