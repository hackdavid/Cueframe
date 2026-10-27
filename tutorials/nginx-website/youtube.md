# YouTube upload details

**Title:** Host Your Own Website on Linux in About a Minute (nginx tutorial)

**Description:**
Install nginx, start it, and publish your own web page on Linux in about a minute. Every step was recorded live on a real Linux machine.

0:00 Intro
0:12 Install nginx with apt
0:23 Check the version and start the server
0:28 Open localhost in the browser
0:37 Publish your own page (one line of HTML)
0:45 Reload and see it live
0:49 Verify with curl (HTTP 200 OK)
0:56 Recap

Commands used:
apt-get install -y nginx
nginx -v && nginx
echo '<h1>Hello from my Linux server</h1>' > /var/www/html/index.html
curl -sI localhost | head -n 1

Made with Cueframe.

**Tags:** nginx, linux, web server, host website, linux tutorial, nginx tutorial, ubuntu, debian, apt, curl, beginner, self hosting

**Captions:** upload nginx-website.srt (English).
**Category:** Science & Technology / Education
