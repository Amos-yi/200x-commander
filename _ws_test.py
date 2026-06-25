"""WS 快速测试 v3"""
import json, time, websocket

def on_open(ws):
    print('Connected')
    ws.send(json.dumps({"time":int(time.time()),"channel":"futures.candlesticks","event":"subscribe","payload":["15m","ETH_USDT"]}))

def on_message(ws, msg):
    d = json.loads(msg)
    evt = d.get("event","")
    if evt == "subscribe":
        print(f"SUB: {json.dumps(d, indent=2)[:300]}")
    elif evt == "update":
        r = d["result"]
        print(f"result type: {type(r).__name__}")
        if isinstance(r, list):
            r = r[0] if r else {}
        print(f"OK! c={r.get('c')} o={r.get('o')} h={r.get('h')} l={r.get('l')} t={r.get('t')} v={r.get('v')}")
        ws.close()

def on_error(ws, err):
    print(f"ERR: {err}")

ws = websocket.WebSocketApp("wss://fx-ws.gateio.ws/v4/ws/usdt", on_open=on_open, on_message=on_message, on_error=on_error)
ws.run_forever(ping_interval=10, ping_timeout=5)
