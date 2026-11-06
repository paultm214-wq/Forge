def answer() -> int:
 return 42
def app(environ,start_response):
 start_response("200 OK",[("Content-Type","text/plain")]);return [b"42"]
