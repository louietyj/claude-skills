# Evaluates JS in a cross-site frame over raw CDP, outside pinchtab:
#   python3 inner_eval.py <part of the frame URL> '<js>'
# Chrome's CDP is on 127.0.0.1:9869 in the sandbox; needs python3-websocket.
import json,sys,urllib.request,websocket
v=json.load(urllib.request.urlopen("http://127.0.0.1:9869/json/version"))
ws=websocket.create_connection(v["webSocketDebuggerUrl"], suppress_origin=True)
i=0
def call(m,p=None,sid=None):
    global i; i+=1; msg={"id":i,"method":m,"params":p or {}}
    if sid: msg["sessionId"]=sid
    ws.send(json.dumps(msg))
    while True:
        r=json.loads(ws.recv())
        if r.get("id")==i: return r
t=[x for x in call("Target.getTargets")["result"]["targetInfos"] if x["type"]=="iframe" and sys.argv[1] in x["url"]][0]
sid=call("Target.attachToTarget",{"targetId":t["targetId"],"flatten":True})["result"]["sessionId"]
print(call("Runtime.evaluate",{"expression":sys.argv[2],"returnByValue":True},sid)["result"])
call("Target.detachFromTarget",{"sessionId":sid})
