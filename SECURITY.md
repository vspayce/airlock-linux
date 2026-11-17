# Security

Airlock is a privacy browser, so security reports matter a lot. Thank you for taking the time.

## Reporting a problem

Please **don't** open a public issue for a security problem. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on https://github.com/vspayce/airlock-linux.

Include what you found, how to reproduce it, and which build you used (the build number is on the Releases page).
You'll get a reply within a week.

## In scope

Anything that defeats one of Airlock's protections: a tracker or ad that gets through a rule meant to block it,
plain-HTTP loads despite HTTPS only, private tabs sharing data, websites reaching your local network,
data surviving Burn, or Airlock's own pages being reachable from a website.

Bugs in the web engine itself (WebKitGTK / GeckoView) belong upstream, but tell us too so we can track the fix.
