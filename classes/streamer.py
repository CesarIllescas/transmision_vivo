import subprocess
from pages.home_view import process
class Streamer:
    def __init__(self, video_output, audio_output, fps, comand):
        self.video_output = video_output
        self.audio_output = audio_output
        self.fps = fps
        self.comand = comand
        self.process = None
        
    def start_streaming(self):
        self.process = subprocess.Popen(self.comand, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process(self.process)

    def stop_streaming(self):
        self.process.terminate()
        self.process = None
        
    def is_streaming(self):
        return self.process is not None
    
    def write(self, data):
        self.process.stdin.write(data)
        self.process.stdin.flush()