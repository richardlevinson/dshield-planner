import os

import multiprocessing as mp


class Supervisor:
    def __init__(self, propel, mode, logLock, loggerQ, sharedDict):
        # TODO: remove mode (always planning)
        propel.mode = mode
        self.propel = propel
        self.mode = mode
        self.targetMethodName = propel.targetProcs[0]
        self.pid = None  # set in supervisorProcess()
        self.appToSupQ = None
        self.sharedDict = sharedDict

        self.logLock = logLock
        self.loggerQ = loggerQ
        propel.planner = self

    def start(self, appToSupQ):
        proc = mp.Process(target=self.supervisorMsgHandler, args=(appToSupQ,))
        proc.start()
        return proc

    def supervisorMsgHandler(self, appToSupQ):
        # The planning supervisor
        self.pid = os.getpid()
        self.appToSupQ = appToSupQ

        done = False

        while not done:
            self.logMsg("waiting for msg")
            msg = appToSupQ.get()  # blocks
            self.logMsg("received msg: " + str(msg))
            msgType = msg["type"]
            if msgType == "start":
                self.propel.parallelMCTS(self.propel.appMethod, self.propel.processCount)
                self.logMsg("\n** Planning complete !! ** \n\nBest score: "+str(self.propel.bestPlanScore))
                self.bestPlanState = self.propel.bestPlanState
                self.sharedDict["bestPlanState"] = self.bestPlanState
                done = True
        self.logMsg("SupervisorMsgHandler() exit")

    def startTargetProc(self):
        targetMethod = getattr(self.propel.target, self.targetMethodName)
        result  =  mp.Process(target=targetMethod, args=(self.mode,))
        result.start()
        return result


    def logMessage(self, msg):
        with self.logLock:
            self.loggerQ.put(msg)

    def logMsgNoHeader(self, msg):
        self.logMessage(msg)

    def logMsg(self, msg, includeBlankLine = False):
        try:
            fullMsg = "" if not includeBlankLine else "\n"
            fullMsg += self.logHeader() + msg
            self.logMessage(fullMsg)
        except:
            print("logMsg() ERROR!")
            print(self.mode +" log ERR: "+msg)

    def logHeader(self, includeFullPid = False):
        pid = os.getpid()
        header = ""
        if pid == self.propel.pid:
            header += "main"
        elif pid == self.pid:
            header += "planner"
        elif self.targetMethodName:
            header += self.targetMethodName

        if includeFullPid:
            header +=" "+ str(pid)
            header += "/"+self.prettyPid(pid)
        else:
            header += " "+self.prettyPid(pid)
        return header+": "

    def prettyPid(self, pid):
        return str(pid - self.propel.pid)


    def __str__(self):
        result = "["+self.mode + " supervisor]"
        return result

