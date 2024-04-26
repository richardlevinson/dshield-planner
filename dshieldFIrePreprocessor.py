import os
import shutil

class DshieldFirePreprocessor:
    def __init__(self):
        self.dataPathRoot = "/Users/richardlevinson/dshieldFireData/"
        self.experiment = "expt2"
        self.experimentRun = "RUN001"
        self.satList = ["CYG41884", "CYG41885", "CYG41886", "CYG41887", "CYG41888", "CYG41889", "CYG41890", "CYG41891"]
        self.experimentDataPath = self.dataPathRoot + self.experiment + "/"
        self.plannerFilepath = self.createPlannerDirectory()
        self.satChoices = {}  #{sat: {tp: {sourceId: [gpList]}}}

    def start(self):
        for sat in self.satList:
            self.readSatGpFile(sat)
            self.readSatGsFiles(sat)
            self.writeSatChoiceFile(sat)
        self.copyGpValueFile()
        print("done")

    def readSatGpFile(self, sat):
        satChoices = {} # {TP: {sourceID: [gpList]}}
        filepath = self.experimentDataPath + "/operator/orbit_prediction/" + self.experimentRun + "/" + sat + "/access/"
        assert os.path.exists(filepath), "readSatGpFile() ERROR! path not found: "+filepath
        filenames = [x for x in os.listdir(filepath) if x.endswith(".csv")]
        if filenames:
            if len(filenames) > 1:
                print("readSatGpFile() ERROR! multiple access files found in "+filepath+ ": "+str(filenames))
            else:
                filename = filenames[0]
        else:
            print("readSatGpFile() ERROR! no access files found in "+filepath)

        header = [] # collect first 4 lines of file as the header
        print("readSatGpFiles() reading GP file for "+sat+ ": "+filename)
        with open(filepath+filename, "r") as f:
            lineNumber = 0
            for line in f:
                line = line.strip()
                lineNumber += 1
                if 1 <= lineNumber and lineNumber <= 4:
                    header.append(line)
                    continue
                line = line.strip()
                if line:
                    tp, sourceId, gpList = line.split(" ")
                    tp = int(tp)
                    sourceId = int(sourceId)
                    gpList = [int(gp) for gp in gpList.split(",")]
                    if tp not in satChoices:
                        satChoices[tp] = {}
                    satChoices[tp].update({sourceId: gpList})
        self.satChoices[sat] = satChoices

    def readSatGsFiles(self, sat):
        satChoices = self.satChoices[sat]# {TP: {"GS": [gsList]}}
        filepath = self.experimentDataPath + "/operator/orbit_prediction/" + self.experimentRun + "/" + sat + "/ground_contact/"
        assert os.path.exists(filepath), "readSatGsFiles() ERROR! path not found: "+filepath
        filenames = os.listdir(filepath)
        assert filenames, "readSatGsFiles() ERROR! no files found found: "+filepath
        header = [] # collect first 4 lines of file as the header
        for filename in filenames:
            with open(filepath+filename, "r") as f:
                gs = None
                lineNumber = 0
                for line in f:
                    line = line.strip()
                    lineNumber += 1
                    if 1 <= lineNumber and lineNumber <= 4:
                        if lineNumber == 1:
                            # strip off GS id from first line
                            gs = line.split(" ")[-1]
                            print("readSatGsFiles() reading GS file for "+sat+ " GS "+ gs+", file: "+filename)
                        header.append(line)
                        continue
                    line = line.strip()
                    if line:
                        start, end = line.split(",")
                        start = int(start)
                        end = int(end)
                        for tp in range(start, end+1):
                            if tp not in satChoices:
                                satChoices[tp] = {"DNL": None}
                            else:
                                print("readSatGsFiles() ERROR! duplicate TP: "+str(tp))
                            satChoices[tp].update({"DNL": gs})
        self.satChoices[sat].update(satChoices)

    def writeSatChoiceFile(self, sat):
        filepath = self.experimentDataPath + "planner/"+self.experimentRun
        if not os.path.exists(filepath):
            print("writeSatChoiceFile() creating dir: "+filepath)
            os.mkdir(filepath)
        filename = filepath + "/"+sat+"_choices.txt"
        tpChoices = self.satChoices[sat]
        sortedTpChoices = sorted(tpChoices.keys())
        priorTP = None
        print("writeSatChoiceFile() "+filename)
        with open(filename, "w") as f:
            for tp in sortedTpChoices:
                if priorTP and tp - priorTP > 1:
                    diffSecs = tp - priorTP
                    gapSize = str(diffSecs)+"s" if diffSecs < 60 else str(round(diffSecs/60, 2))+"m"
                    f.write("\n--- GAP "+str(gapSize)+" ---\n")
                priorTP = tp
                f.write(str(tp)+": "+str(tpChoices[tp])+"\n")

    def copyGpValueFile(self):
        srcFilepath = self.experimentDataPath + "target_value/" + self.experimentRun + "/"
        filenames = os.listdir(srcFilepath)
        if filenames:
            if len(filenames) > 1:
                print("copyGpValueFile() ERROR! multiple value files found in "+srcFilepath+ ": "+str(filenames))
            else:
                filename = filenames[0]
                srcFilepath += filename
                destFilepath = self.plannerFilepath + self.experimentRun + "/" + filename
                print("copyGpValueFile() "+destFilepath)
                shutil.copyfile(srcFilepath, destFilepath)
        else:
            print("copyGpValueFile() ERROR! no value files found in "+srcFilepath)

    def createPlannerDirectory(self):
        self.plannerFilepath = self.experimentDataPath + "planner/"
        if not os.path.exists(self.plannerFilepath):
            print("createPlannerDirectory() creating dir: "+self.plannerFilepath)
            os.mkdir(self.plannerFilepath)
        return self.plannerFilepath

def main():
    preprocessor = DshieldFirePreprocessor()
    preprocessor.start()

if __name__ == '__main__':
    main()