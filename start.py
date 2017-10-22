#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Damn simple IPTV Server for Kodi's IPTV Simple client addon
#
# Install
#  $ python3 -m pip install flask --user
# Start
#  $ FLASK_APP=start.py python3 -m flask run --host=0.0.0.0
# Start(Debug Mode)
#  $ FLASK_APP=start.py FLASK_DEBUG=1 python3 -m flask run --host=0.0.0.0
from flask import Flask, Response, request, jsonify, stream_with_context
import logging, subprocess, threading, multiprocessing, os, sys, datetime, glob
from logging.handlers import RotatingFileHandler
from time import strftime, sleep
import traceback
import MySQLdb

app = Flask(__name__)
currentChannel = ""
currentSID = ""
isPlaying = False
lastPlaylistLoadTime = datetime.datetime.now()
gsproc = ""

@app.route('/')
def pong():
    return 'Pong'

@app.route('/getChannels')
def getChannels():

    retstr = '#EXTM3U\n'

    db = MySQLdb.connect(host="localhost",user="epgrec",passwd="epgrec",db="epgrec",charset="utf8")
    cur = db.cursor()
    cur.execute("""select * from Recorder_channelTbl where skip <> 1""")
    rows = cur.fetchall()
    for row in rows:
       ch = row[2]
       sid = row[5]
       cname = row[3]
       retstr += '#EXTINF:-1 tvg-id="' + str(ch) + "_" + str(sid) + '",' + str(cname) + '\n'
       retstr += 'http://epgrec2.mkhome:5000/startView?channel=' + str(ch) + '-' + str(sid) + '\n'
    return retstr

@app.route('/startView')
def startView():
    global currentChannel, currentSID
    global isPlaying
    global lastPlaylistLoadTime
    global gsproc
    HOMENET="192.168.20"
    cmd1str = ""
    cmd2str = ""
    (channel, sid) = request.args.get('channel', ('', '')).split('-')
    if channel == '':
       return "Channel not defined", 501
    else:
       cmd1str = "recpt1 --b25 --strip --sid " + sid + " " + channel + " - -"
    if HOMENET in request.remote_addr:
       cmd2str = "ffmpeg -i pipe:0 -map 0:v -map 0:a -c:v copy -c:a copy "
    else:
       #cmd2str = "ffmpeg -i pipe:0 -map 0:v -map 0:a -c:v hevc -preset ultrafast -vf yadif -sc_threshold 80 -b:v 384k -c:a aac -b:a 32k "
       cmd2str = "ffmpeg -i pipe:0 -map 0:v -map 0:a -c:v libx264 -preset ultrafast -vf yadif -sc_threshold 80 -b:v 384k -c:a aac -b:a 32k "
    cmd2str += "-f segment -hls_flags delete_segments -segment_list_size 10 -segment_format mpegts -segment_time 10 "
    cmd2str += "-segment_list /var/www/html/tv/playlist.m3u8 /var/www/html/tv/stream%04d.ts"

    def generateStream(cmd1str, cmd2str):
       p1 = subprocess.Popen(cmd1str.split(), stdout=subprocess.PIPE)
       p2 = subprocess.Popen(cmd2str.split(), stdin=subprocess.PIPE)

       while p1.poll() is None:
          for c in iter(p1.stdout.readline, b''):
             p2.stdin.write(c)
          p1.stdout.flush()
       p2.terminate()

    def stopStream(playproc):
       sys.stderr.write("\nstop\n")
       playproc.terminate()
       while playproc.is_alive():
          sleep(1)
       for f in glob.glob("./stream*.ts"):
          if os.path.exists(f):
             os.remove(f)
       if os.path.exists("playlist.m3u8"):
          os.remove("playlist.m3u8")
       isPlaying = False

    def autoStopStream(a, playproc):
       global lastPlaylistLoadTime
       global isPlaying
       global currentChannel
       global currentSID
       now = datetime.datetime.now()
       limit = lastPlaylistLoadTime + datetime.timedelta(seconds=20)
       if now > limit:
          sys.stderr.write("\nautostop\n")
          playproc.terminate()
          while playproc.is_alive():
             sleep(1)
          for f in glob.glob("./stream*.ts"):
             if os.path.exists(f):
                os.remove(f)
          if os.path.exists("./playlist.m3u8"):
             os.remove("./playlist.m3u8")
          isPlaying = False
          currentChannel = ""
          currentSID = ""
    
    if isPlaying != True:
       isPlaying = True
       currentChannel = channel
       currentSID = sid
       gsproc = multiprocessing.Process(target=generateStream, name="gsthread", args=(cmd1str, cmd2str))
       gsproc.start()
    else:
       if currentChannel != channel or currentSID != sid:
          stopStream(gsproc)
          isPlaying = True
          currentChannel = channel
          currentSID = sid
          gsproc = multiprocessing.Process(target=generateStream, name="gsthread", args=(cmd1str, cmd2str))
          gsproc.start()

    lastPlaylistLoadTime = datetime.datetime.now()

    while not os.path.exists("/var/www/html/tv/playlist.m3u8"):
       sleep(1)

    stopTimer = threading.Timer(20, autoStopStream, args=(1, gsproc))
    stopTimer.start()

    def streamPlaylist(fn):
       with open(fn) as fh:
          for i in [1,2,3,4,5]:
             line = fh.readline()
             yield line
          lineodd = True
          for line in fh:
             if lineodd == True:
                yield line
                lineodd = False
             else:
                line = "http://epgrec2.mkhome/tv/" + line
                yield line
                lineodd = True

    return Response(streamPlaylist("/var/www/html/tv/playlist.m3u8"))
   
@app.after_request
def after_request(response):
    # This IF avoids the duplication of registry in the log,
    # since that 500 is already logged via @app.errorhandler.
    if response.status_code != 500:
        ts = strftime('[%Y-%b-%d %H:%M:%S]')
        logger.error('%s %s %s %s %s %s',
                      ts,
                      request.remote_addr,
                      request.method,
                      request.scheme,
                      request.full_path,
                      response.status)
    return response

@app.errorhandler(Exception)
def exceptions(e):
    ts = strftime('[%Y-%b-%d %H:%M:%S]')
    tb = traceback.format_exc()
    logger.error('%s %s %s %s %s 5xx INTERNAL SERVER ERROR\n%s',
                  ts,
                  request.remote_addr,
                  request.method,
                  request.scheme,
                  request.full_path,
                  tb)
    return "Internal Server Error", 500

if __name__ == '__main__':
    # The maxBytes is set to this number, in order to demonstrate the generation of multiple log files (backupCount).
    handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=3)
    # getLogger('__name__') - decorators loggers to file / werkzeug loggers to stdout
    # getLogger('werkzeug') - werkzeug loggers to file / nothing to stdout
    logger = logging.getLogger('__name__')
    logger.setLevel(logging.ERROR)
    logger.addHandler(handler)
    app.run(host='0.0.0.0',port=5000)
else:
    # The maxBytes is set to this number, in order to demonstrate the generation of multiple log files (backupCount).
    handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=3)
    # getLogger('__name__') - decorators loggers to file / werkzeug loggers to stdout
    # getLogger('werkzeug') - werkzeug loggers to file / nothing to stdout
    logger = logging.getLogger('__name__')
    logger.setLevel(logging.ERROR)
    logger.addHandler(handler)

