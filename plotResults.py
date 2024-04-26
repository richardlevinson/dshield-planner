import ast
import os
import numpy as np
import matplotlib.pyplot as plt

# x-axis in ["time", "rollouts", "time+rollouts"]
# y-axis in ['observed', 'downlinked','latency (avg)','image count', 'max depth', 'target value (avg)'}


class Plotter:
    def __init__(self):
        # self.dir = "./"
        # self.dir = "/Users/richardlevinson/dshieldFireData/expt2/planner/RUN001/results/7.19.23/"
        # self.dir = "/Users/richardlevinson/dshieldFireData/expt2/planner/RUN001/results/8.11.23/"
        self.dir = "/Users/richardlevinson/dshieldFireData/expt2/planner/RUN001/results/11.24.23/"
        self.config = {"x-axis": "time", "y-axis": ["observed", "downlinked"], "type": "line"}
        self.includeRollouts = "ALL" #"[1000, 3000]
        # self.includeRollouts = [100, 1000, 2000, 5000, 10000, 20000, 30000, 40000, 50000, 100000, 200000] #"ALL" # [1, 100, 30000]
        self.data = []
        self.targetValues = {}
        self.isTimeVrolls =True #True
        self.satCounts = []
        self.procCounts = []

    def run(self):
        # self.readTargetValues()
        # self.showTargetValueHistogram(observedTargets200K) #observedTargets)
        # return
        self.readResults()
        self.filterData()
        self.drawPlot()

    def readResults(self):
        file = self.dir+"results.txt"
        with open(file, "r") as f:
            for line in f:
                result = ast.literal_eval(line)
                self.data.append(result)
                satCount = result["satCount"]
                if satCount not in self.satCounts:
                    self.satCounts.append(satCount)
                procCount = result["procs"]
                if procCount not in self.procCounts:
                    self.procCounts.append(procCount)

    def filterData(self):
        filteredData = []
        for result in self.data:
            rollout = result["rollouts"]
            if self.includeRollouts == "ALL" or rollout in self.includeRollouts:
                filteredData.append(result)
        self.data = filteredData

    def drawPlot(self):
        xAxis = self.config["x-axis"]
        yAxis = self.config["y-axis"]
        xMax = 0
        chartType = self.config["type"]
        self.procCounts.sort()
        procCountResults = {}
        for procCount in self.procCounts:
            results = []
            for result in self.data:
                if procCount == result["procs"]:
                    results.append(result)
            procCountResults[procCount] = results
        # self.satCounts.sort()
        # satCountResults = {}
        # group results by satCount
        # for satCount in self.satCounts:
        #     results = []
        #     for result in self.data:
        #         if satCount == result["satCount"]:
        #             results.append(result)
        #     satCountResults[satCount] = results
        # x = [p[xAxis.lower()] for p in self.data]
        # for satCount in self.satCounts:
        #     results = satCountResults[satCount]
        for procCount in self.procCounts:
            results = procCountResults[procCount]
            if xAxis == "time+rollouts":
                x =[]
                for result in results:
                    row1 = str(int(round(result["time"],0))) #+"m"
                    rolls = result["rollouts"]
                    pCount = result["procs"]
                    if rolls >= 1000:
                        row2 = str(int(rolls/1000))+"k ("+str(pCount)+" procs)"
                    else:
                        row2 = str(rolls)
                    x.append(row1+"\n"+row2)

                # x = [str(int(round(p["time"],0)))+"\n"+str(p["rollouts"]) for p in self.data]
                # x = [str(p["time"])+"\n("+str(p["rollouts"])+" rolls)" for p in self.data]
            elif xAxis == "time":
                x = []
                for result in results:
                    # result = self.data[key]
                    # x.append(str(int(round(result["time"],0))))
                    x.append(int(round(result["time"], 0)))
                # x = [str(p["time"]) for p in self.data]
            elif xAxis == "rollouts":

                if True: #self.isTimeVrolls:
                    x = [p["rollouts"] for p in results]
                else:
                    x =[]
                    for p in results:
                        rolls = p["rollouts"]
                        if rolls >= 1000:
                            row = str(int(rolls/1000))+"k"
                        else:
                            row = str(rolls)
                        x.append(row)
            xMax = max(xMax, max(x))
            yAxes = []
            yAxisLabel = ""
            for y in yAxis:
                yLegend = y+" "+str(procCount)+" procs"
                if y == "objective":
                    yAxes.append((yLegend, [int(round(p[y],0)) for p in results]))
                elif self.isTimeVrolls:
                    yAxes.append((yLegend, [int(round(p[y],0)) for p in results]))
                else:
                    yAxes.append((yLegend, [round(p[y],2) for p in results]))
                if yAxisLabel:
                    # case of multiple y axes
                    yAxisLabel += " and "
                if y == "target value (avg)":
                    yAxisLabel = "Observed "+ yAxisLabel
                elif y == "latency (avg)":
                    y = "latency minutes (avg)"
                elif y == "max depth":
                    y = "Max learning depth"
                elif y == "time":
                    y = "Planning time (minutes)"
            if "observed" in yAxis and "downlinked" in yAxis:
                yAxisLabel = "Observed & Downlinked Targets"
            else:
                yAxisLabel += y.capitalize()

            for yLabel, yValues in yAxes:
                yLabel = yLabel.capitalize()
                if yLabel == "latency":
                    yLabel += " (minutes)"
                if chartType == "bar":
                    plt.bar(x, yValues, label = yLabel) #, width=0.1)
                elif chartType == "line":
                    plt.plot(x,yValues,marker='o', label = yLabel)
                elif chartType == "scatter":
                    plt.scatter(x,yValues, label = yLabel)
                for i, j in zip(x, yValues): # Add data labels to each bar
                    plt.text(i, j, str(j), ha='center', va='bottom')

        if xAxis == "time":
            xAxis = "Planning Time (minutes)"
        elif xAxis == "time+rollouts":
            xAxis = "Planning Time (minutes)\n# Rollouts"
        else:
            xAxis = xAxis.capitalize()
        # plt.xticks(np.arange(0, xMax, 200))
        plt.xlabel(xAxis)
        plt.ylabel(yAxisLabel)
        plt.title(yAxisLabel)
        plt.legend()
        plt.grid()
        plt.show()

    def showTargetValueHistogram(self, targets=None):
        if not targets:
            values = list(self.targetValues.values())
        else:
            values = []
            for target in targets:
                values.append(self.targetValues[target])

        n, bins, _ = plt.hist(x=values)

        # Plot the label/text for each bin
        for i in range(len(n)):
            x_pos = (bins[i + 1] - bins[i]) / 4 + bins[i]
            y_pos = n[i] + (n[i] * 0.01)
            label = str(int(n[i]))
            plt.text(x_pos, y_pos, label)

        plt.title("Observed targets (" + str(len(values)) + ")      200,000 rollouts")
        plt.xlabel("Target value")
        plt.ylabel("Target count")
        ax = plt.gca()
        ax.set_ylim([0, 120])
        plt.show()

    def readTargetValues(self):
        dataPathRoot = "/Users/richardlevinson/dshieldFireData/"
        experiment = "expt1b"
        experimentRun = "RUN001"
        experimentDataPath = dataPathRoot + experiment+"/"
        plannerFilepath = experimentDataPath + "planner/"
        filepath = plannerFilepath + experimentRun+"/"
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
                    self.targetValues[gp] = value

observedTargets200K = [22023, 22024, 22330, 22331, 22332, 22941, 23260, 23261, 23267, 23573, 23574, 23889, 23890, 24229, 24558, 24561, 24563, 24564, 24682, 24683, 24688, 24689, 24690, 24691, 24693, 24694, 24696, 24697, 24700, 24702, 24703, 24704, 24706, 24708, 24714, 24716, 24717, 24719, 24892, 24893, 25013, 25015, 25019, 25020, 25021, 25022, 25023, 25025, 25026, 25028, 25029, 25032, 25034, 25035, 25036, 25038, 25040, 25046, 25049, 25052, 25557, 25558, 25872, 25873, 25874, 25875, 25876, 25877, 25878, 25879, 25880, 25881, 26200, 26201, 26202, 26203, 26204, 26205, 26206, 26207, 26208, 26209, 26210, 26211, 26212, 26534, 26535, 26536, 26537, 26538, 26539, 26540, 26541, 26542, 26543, 26544, 26545, 26546, 26547, 26859, 26860, 26861, 26862, 26863, 26864, 26865, 26866, 26867, 26868, 26869, 26870, 26871, 27191, 27192, 27193, 27194, 27195, 27196, 27197, 27275, 27276, 27277, 27278, 27279, 27281, 27282, 27284, 27285, 27286, 27287, 27288, 27289, 27290, 27291, 27292, 27293, 27299, 27301, 27302, 27305, 27308, 27593, 27597, 27598, 27600, 27606, 27608, 27609, 27611, 27612, 27614, 27615, 27618, 27619, 27620, 27621, 27622, 27623, 27624, 27630, 27632, 27633, 27636, 27639, 27896, 27900, 27901, 27906, 27921, 27923, 27928, 27929, 27931, 27932, 28152, 28154, 28158, 28193, 28194, 28195, 28196, 28198, 28200, 28202, 28208, 28212, 28213, 28219, 28466, 28467, 28468, 28469, 28470, 28471, 28472, 28497, 28498, 28503, 28506, 28508, 28509, 28511, 28516, 28782, 28783, 28784, 28785, 28803, 28807, 28809, 30040, 30347, 30419, 30700, 30701, 30703, 30704, 30705, 30706, 30707, 30708, 30709, 30711, 30712, 30713, 30720, 30721, 30992, 30995, 31001, 31003, 31004, 31006, 31007, 31009, 31010, 31011, 31012, 31013, 31014, 31015, 31017, 31019, 31282, 31286, 31289, 31294, 31295, 31296, 31297, 31298, 31299, 31324, 31326, 31328, 31334, 31337, 31340, 31342, 31343, 31552, 31553, 31554, 31559, 31590, 31593, 31595, 31596, 31598, 31599, 31601, 31602, 31605, 31607, 31608, 31609, 31610, 31611, 31613, 31628, 31629, 31825, 31826, 31828, 31832, 31849, 31859, 31863, 31866, 31870, 31872, 31873, 31875, 31876, 31878, 31879, 31881, 31883, 31884, 31885, 31886, 31887, 32097, 32112, 32116, 32117, 32122, 32132, 32136, 32137, 32138, 32380, 32381, 32383, 32385, 32387, 32394, 32398, 32399, 32403, 32670, 32672, 32673, 32674, 32676, 32677, 32690, 32692, 32693, 32696, 32698, 32699, 32959, 32960, 32961, 32970, 32971, 32972, 32973, 32974, 32975, 32980, 32983, 32986, 32989, 33247, 33248, 33249, 33251, 33252, 33253, 33254, 33255, 33256, 33257, 33258, 33260, 33261, 33262, 33281, 33525, 33526, 33528, 33537, 33538, 33540, 33542, 33548, 33550, 33551, 33557, 33558, 33800, 33809, 33810, 33812, 33814, 33816, 33817, 33818, 33819, 33820, 33822, 33824, 34078, 34079, 34080, 34081, 34085, 34086, 34087, 34088, 34089, 34090, 34091, 34093, 34094, 34098, 34099, 34100, 34352, 34354, 34356, 34357, 34358, 34361, 34362, 34399, 34401, 34404, 34633, 34634, 34636, 34637, 34638, 34671, 34673, 34674, 34676, 34677, 34679, 34680, 34682, 34683, 34685, 34688, 34928, 34929, 34953, 34954, 34955, 34956, 34957, 34958, 34959, 34960, 34961, 34962, 34963, 34964, 34965, 34967, 34969, 34972, 34974, 34976, 35215, 35216, 35217, 35218, 35230, 35231, 35232, 35233, 35234, 35235, 35236, 35237, 35238, 35240, 35241, 35242, 35243, 35244, 35245, 35246, 35247, 35248, 35250, 35495, 35497, 35498, 35501, 35502, 35503, 35504, 35505, 35506, 35507, 35508, 35509, 35510, 35511, 35512, 35513, 35515, 35760, 35761, 35762, 35763, 35764, 35765, 35767, 35768, 35769, 35770, 36016, 36019, 36022, 36023, 36274, 36550, 36810, 37327, 37328, 37329, 37330, 37331, 37572, 38586, 38587, 38826, 38827, 38828, 38829, 38830, 38831, 39065, 39066, 39067, 39068, 39069, 39070, 39071, 39072, 39294, 39317, 39318, 39319, 39320, 39321, 39322, 39323, 39324, 39325, 39564, 39565, 39566, 39567, 39568, 39569, 39570, 39817, 39818, 39819, 39820, 39821, 39822, 39823, 40089, 40090, 40091, 40092, 40093, 40331, 40332, 40333, 40334, 40568, 40571, 40572, 40817, 40818, 40819, 40820, 41310, 41594, 41598, 41601, 41819, 41820, 41822, 41823, 41824, 41825, 41826, 41827, 41828, 41829, 41831, 41837, 41839, 41840, 41843, 41846, 42053, 42057, 42059, 42060, 42061, 42062, 42063, 42064, 42066, 42067, 42069, 42070, 42072, 42073, 42074, 42075, 42076, 42077, 42078, 42079, 42280, 42281, 42283, 42287, 42289, 42290, 42291, 42292, 42294, 42295, 42514, 42515, 46330, 46331, 46355, 46356, 46357, 46383, 46405, 46406, 46407, 46408, 46409, 46427, 46428, 46429, 46430, 46431, 46449, 46450, 46465, 46466, 46468, 46469, 46470, 46472, 46473, 46487, 46488, 46489, 46490, 46492, 46493, 46494, 46495, 46497, 46498, 46506, 46507, 46508, 46509, 46510, 46516, 46517, 46518, 46522, 46523, 46524, 46533, 46534, 46535, 46536, 46537, 46548, 46549, 46550, 46558, 46559, 46560, 46561, 46562, 46573, 46574, 46575, 46579, 46580, 46581, 46582, 46583, 46595, 46596, 46604, 46605, 46619, 46731, 46732, 46733, 46742, 46743, 46744, 46745, 46754, 46755, 46756, 46757, 46758, 46762, 46763, 46764, 46765, 46766, 46769, 46770, 46771, 46775, 46776, 46777, 46780, 46782, 46783, 46784, 46785, 46786, 46787]

observedTargets1K = [23259, 23260, 23571, 23572, 23573, 23887, 23888, 23889, 23890, 24228, 24229, 24230, 24231, 24558, 24559, 24560, 24561, 24562, 24563, 24564, 24892, 24893, 25557, 25558, 25872, 25873, 25874, 25875, 25876, 25877, 25878, 25879, 25880, 25881, 26200, 26201, 26202, 26203, 26204, 26205, 26206, 26207, 26208, 26209, 26210, 26211, 26534, 26535, 26536, 26537, 26538, 26539, 26540, 26541, 26542, 26543, 26544, 26545, 26859, 26860, 26861, 26862, 26863, 26864, 26865, 26866, 26867, 26868, 26869, 26870, 27191, 27192, 27193, 27194, 27195, 27196, 27896, 27897, 27898, 27899, 27900, 27901, 28152, 28193, 28194, 28195, 28196, 28197, 28198, 28199, 28200, 28201, 28202, 28203, 28204, 28205, 28206, 28207, 28208, 28209, 28210, 28211, 28212, 28213, 28466, 28467, 28468, 28469, 28470, 28471, 28497, 28498, 28499, 28500, 28501, 28502, 28503, 28504, 28505, 28506, 28507, 28508, 28509, 28510, 28511, 28512, 28513, 28514, 28515, 28516, 28782, 28783, 28784, 28785, 28798, 28799, 28800, 28801, 28802, 28803, 28804, 28805, 28806, 28807, 28808, 28809, 29108, 29109, 30040, 30347, 31552, 31553, 31554, 31825, 31826, 31827, 31828, 31829, 31830, 31831, 31832, 32096, 32097, 32098, 32099, 32108, 32109, 32110, 32111, 32112, 32113, 32114, 32115, 32116, 32380, 32381, 32382, 32383, 32384, 32385, 32386, 32387, 32388, 32389, 32390, 32391, 32392, 32393, 32394, 32395, 32396, 32397, 32398, 32669, 32670, 32671, 32672, 32673, 32674, 32675, 32676, 32677, 32678, 32679, 32958, 32959, 32960, 32961, 33244, 34078, 34079, 34352, 34633, 34928, 34929, 35215, 35216, 35217, 35218, 35495, 36016, 36017, 36019, 36274, 36550, 36807, 36808, 36810, 37064, 37327, 37328, 37329, 37330, 37331, 37572, 38586, 38587, 38826, 38827, 38828, 38829, 38830, 38831, 39065, 39066, 39067, 39068, 39069, 39070, 39071, 39072, 39294, 39317, 39318, 39319, 39320, 39321, 39322, 39323, 39324, 39325, 39564, 39565, 39566, 39567, 39568, 39569, 39570, 39817, 39818, 39819, 39820, 39821, 39822, 39823, 40089, 40090, 40091, 40092, 40093, 40331, 40332, 40333, 40334, 40571, 40572, 40817, 40818, 40819, 40820, 41310, 46330, 46331, 46355, 46356, 46357, 46383, 46405, 46406, 46407, 46408, 46409, 46427, 46428, 46429, 46430, 46431, 46449, 46450, 46465, 46466, 46468, 46469, 46470, 46472, 46473, 46487, 46488, 46489, 46490, 46492, 46493, 46494, 46495, 46497, 46498, 46506, 46507, 46508, 46509, 46510, 46516, 46517, 46518, 46522, 46523, 46524, 46533, 46534, 46535, 46536, 46537, 46548, 46549, 46550, 46558, 46559, 46560, 46561, 46562, 46573, 46574, 46575, 46579, 46580, 46581, 46582, 46583, 46595, 46596, 46604, 46605, 46619, 46731, 46732, 46733, 46742, 46743, 46744, 46745, 46754, 46755, 46756, 46757, 46758, 46762, 46763, 46764, 46765, 46766, 46769, 46770, 46771, 46775, 46776, 46777, 46780, 46782, 46783, 46784, 46785, 46786, 46787]

Plotter().run()
