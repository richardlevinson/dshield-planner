import ast
import os

class DataCollector:
    def __init__(self):
        # self.dir = "/Users/richardlevinson/dshieldFireData/expt2/planner/RUN001/results/7.19.23/"
        self.dir = "/Users/richardlevinson/dshieldFireData/expt2/planner/RUN001/results/8.11.23/"
        self.files = []
        self.data = {}

    def run(self):
        self.collectFiles(self.dir)
        print("files ("+str(len(self.files))+"):\n"+str(self.files))
        for file in self.files:
            results = self.parseLogFile(file)
            rollouts = results["rollouts"]
            satCount = results["satCount"]
            if rollouts in self.data:
                rolloutDicts = self.data[rollouts]
            else:
                rolloutDicts = {}
            rolloutDicts[satCount] = results
            self.data[rollouts] = rolloutDicts
        self.writeResultsToFile()

    def collectFiles(self, directory):
        # RECURSIVE
        if not directory.endswith("/"):
            directory += "/"
        files = os.listdir(directory)
        for file in files:
            filepath = directory + file
            if os.path.isdir(filepath):
                self.collectFiles(filepath) # recursive
            elif "plannerlog" in filepath.lower():
                self.files.append(filepath)

    def parseLogFile(self, file):
        print("parseLogFile() file: "+file)
        satCount = None
        rollouts = None
        objective = None
        heuristic = None
        totalMins = None
        observedTotal = 0
        downlinkedTotal = 0
        with open(file, "r") as f:
            for line in f:
                l = line.lower().strip()
                if "satellites: " in l:
                    terms = l.split("satellites: ")
                    term = terms[-1].strip()
                    satCount = int(term)
                elif "rollouts" in l:
                    terms = l.split("(")
                    term = terms[-1]
                    rollouts, _ = term.split(" ")
                    rollouts = int(rollouts)
                elif "best score: " in l and "*," in l:
                    terms = l.split("best score: ")
                    terms = terms[1].split(',')
                    objective = float(terms[0][:-1])
                elif l.startswith("time: ") and "elapsed" in l:
                    h = 0
                    m = 0
                    s = 0
                    terms = l.split("elapsed: ")
                    time = terms[-1]
                    timeTerms = time.split(", ")
                    for timeTerm in timeTerms:
                        val, units = timeTerm.split(" ")
                        if units == "h":
                            h = float(val)
                        elif units == "m":
                            m = float(val)
                        elif units == "s":
                            s = float(val)
                    totalSecs = (h*3600) + (m*60) + s
                    totalMins = round(totalSecs/60,3)
                elif "downlinked targets" in l:
                    terms = l.split("(")
                    term = terms[-1]
                    downlinked, observed = term.split("/")
                    downlinked = int(downlinked)
                    observed = int(observed[:-1])
                    observedTotal += observed
                    downlinkedTotal += downlinked
                elif "heuristic" in l:
                    terms = l.split("heuristic: ")
                    satsTerm = terms[0]
                    satCount = int(satsTerm.split(",")[0].split(" ")[-1])
                    heuristicTerm = terms[1]
                    if heuristicTerm.startswith("<bound method"):
                        heuristic = "greedy"
        avgLatency, imageCount = self.calculateLatencyAndImageCount(file)
        avgTargetValue = round(objective/observed, 3)
        result = {"rollouts": rollouts, "satCount": satCount, "objective": objective, "heuristic": heuristic, "time": totalMins, "observed": observedTotal, "downlinked": downlinkedTotal, "latency (avg)": avgLatency, "image count": imageCount, "target value (avg)": avgTargetValue}
        # maxDepth = self.collectMaxDepth(file)
        # result = {"rollouts": rollouts, "satCount": satCount, "objective": objective, "heuristic": heuristic, "time": totalMins, "observed": observedTotal, "downlinked": downlinkedTotal, "latency (avg)": avgLatency, "image count": imageCount, "max depth": maxDepth, "target value (avg)": avgTargetValue}
        return result

    def calculateLatencyAndImageCount(self, filepath):
        # latency is in minutes
        dir = os.path.dirname(filepath)
        files = os.listdir(dir)
        imageFiles = []
        for file in files:
            if file.endswith(".imageInfo.txt"):
                imageFiles.append(file)
        latencies = []
        imageCount = 0
        for file in imageFiles:
            imageFilepath = dir + "/"+file
            print("reading image file: "+file)
            with open(imageFilepath, "r") as f:
                for line in f:
                    imageCount += 1
                    imageInfo = ast.literal_eval(line)
                    if "latency" in imageInfo:
                        latencies.append(imageInfo["latency"])
        totalLatency = sum(latencies)
        avgLatency = totalLatency/len(latencies) # seconds
        avgLatency = avgLatency /60 # minutes
        return (round(avgLatency, 3), imageCount)

    def collectMaxDepth(self, filepath):
        maxDepth = 0
        dir = os.path.dirname(filepath)
        imageFilepath = dir + "/searchTree.txt"
        with open(imageFilepath, "r") as f:
            for line in f:
                if "depth:" in line:
                    l = line.strip()
                    pos = l.find("depth: ")
                    if pos > 0:
                        terms = l[pos+len("depth: "):].split(",")
                        depth = int(terms[0].strip())
                        if depth > maxDepth:
                            maxDepth = depth
        return maxDepth

    def writeResultsToFile(self):
        sortedResults = sorted(self.data.keys())
        file = self.dir+"results.txt"
        with open(file, "w") as f:
            # f.write("# rollouts, objective, time (m), observed, downlinked, heuristic\n")
            for rolloutCount in sortedResults:
                row = self.data[rolloutCount]
                # msg = str(row["rollouts"])+","+str(row["objective"])+","+str(row["time"])+","+str(row["observed"])+","+str(row["downlinked"])+","+str(row["heuristic"])
                for satCount in row:
                    f.write(str(row[satCount])+"\n")
                # f.write("{"+str(key)+": "+str(row)+"}\n")


dc = DataCollector()
dc.run()
