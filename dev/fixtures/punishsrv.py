# Mock of AliExpress's punish overlay (see headless-browser/README.md, Debugging).
# Serves on :8766: /item/<n>.html is an item page with the punish iframe, whose
# inner frame renders a reCAPTCHA Enterprise anchor and a __recaptchaValidateCB__
# that removes the overlay. /item/4.html's callback then goes on to /item/5.html
# (a chained second challenge); /item/late.html adds the iframe 1 s after load;
# /item/9.html redirects to a punish page whose slider refuses every drag.
import http.server, socketserver
ANCHOR = "https://www.google.com/recaptcha/enterprise/anchor?ar=1&k=6LcsZOwpAAAAAFfDsdu7pUv7GeN-Asc1Lzeo5LYP&co=aHR0cHM6Ly9yZWNvbS1hY3MuYWxpZXhwcmVzcy51czo0NDM.&hl=en&v=x&size=normal&sa=Ab8FoKfjyPupWZQXwvYp9oBmXvHSYGNDvN2ghbwpv0ak&cb=1"
LATE = '<html><body><h1>item page</h1><script>setTimeout(function(){var f=document.createElement("iframe");f.id="pf";f.style.cssText="width:400px;height:300px";f.src="/h5/x/1.0/_____tmd_____/punish?x5secdata=abc";document.body.appendChild(f)},1000);setTimeout(function(){document.title="Item"},2000);</script></body></html>'
SLIDER = ('<html><head><title>Captcha Interception</title></head><body><p>Please drag the slider to verify</p>'
          '<div id="nc_1_n1t" style="position:absolute;left:500px;top:300px;width:300px;height:34px;background:#eee">'
          '<span id="nc_1_n1z" style="position:absolute;left:0;top:2px;width:42px;height:30px;background:#f60"></span></div>'
          '<script>document.addEventListener("mouseup",function(){var h=document.getElementById("nc_1_n1z");if(h){h.remove();'
          'document.body.insertAdjacentHTML("beforeend","<p>Oops... something is wrong. Please refresh and try again.(error:MOCK1)</p>")}})</script></body></html>')
TOP = '<html><body><h1>item page</h1><iframe id="pf" style="width:400px;height:300px" src="/h5/x/1.0/_____tmd_____/punish?x5secdata=abc"></iframe></body></html>'
OUTER = '<html><body>outer<iframe style="width:380px;height:280px" src="/h5/x/1.0/_____tmd_____/punish?recaptcha=1&iframe=1&x5step=3&x5secdata=abc"></iframe></body></html>'
INNER = ('<html><body><div id="captcha"><iframe src="%s"></iframe></div><textarea id="g-recaptcha-response"></textarea>'
         '<script>window.__recaptchaValidateCB__=function(r){var t=window.top;t.__got=r;t.document.getElementById("pf").remove();if(t.location.pathname=="/item/4.html")t.setTimeout(`location.href="/item/5.html"`,1500);};</script></body></html>') % ANCHOR
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/item/9.html':
            self.send_response(302); self.send_header('Location','/item/9.html/_____tmd_____/punish?x5secdata=mock&x5step=1'); self.end_headers(); return
        if self.path.startswith('/item/9.html/_____tmd_____/punish'):
            self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(SLIDER.encode()); return
        body = LATE if self.path.startswith('/item/late') else TOP if self.path.startswith('/item') else INNER if 'recaptcha=1' in self.path else OUTER if 'punish' in self.path else None
        if body is None: self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(body.encode())
    def log_message(self,*a): pass
socketserver.TCPServer.allow_reuse_address=True
socketserver.TCPServer(('',8766),H).serve_forever()
