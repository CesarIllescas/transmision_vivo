from typing import Union
import flet as ft
from pages.Router import Router, DataStrategyEnum
from State import global_state, State
import cv2
import numpy as np
import threading
import base64
import subprocess
import selectors
import numpy as np
import time
import flet.canvas as cv
import os
import platform
import re
import queue
import pyaudio
# import sounddevice as sd
import numpy as np
import wave
import errno
import sqlite3
from db.db_conn import connect_sqlite, create_table, get_transmisiones, insert_transmision

PLATFORM = platform.system()
AUDIO_OUTPUT = "statics/audio_output.wav"
if PLATFORM == 'Windows':
    import win32pipe
    import win32file

channels = 2
rate = 44100
chunk = 1024


global curtain
curtain = False
global audio_pipe_created
audio_pipe_created = False
global video_pipe_created
video_pipe_created = False
audio_process = None
audio_queue = queue.Queue(maxsize=100)
video_queue = queue.Queue(maxsize=100)


def microphone_audio_stream(pipe_name, event, current_curtain_selected, audio_device_index=None):
    global audio_pipe
    global curtain
    global audio_pipe_created
    audio_pipe = create_pipe(pipe_name)
    audio_pipe_created = True
    print("audio pipe creado")
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16,
                        channels=1,
                        rate=rate,
                        input=True,
                        frames_per_buffer=chunk,
                        input_device_index=audio_device_index)
    if platform.system() == 'Windows':
        connect_pipe(audio_pipe)
    try:
        while True:
            if platform.system() == 'Windows':
                win32file.WriteFile(audio_pipe, audio_data)
            else:
                if curtain:
                    with open(AUDIO_OUTPUT, 'rb') as f:
                        while curtain:
                            audio_data = f.read(chunk * 2 * channels)
                            if not audio_data:
                                f.seek(0)
                                continue
                            if platform.system() == 'Windows':
                                timestamp = time.time()
                                audio_queue.put((timestamp, audio_data))
                                win32file.WriteFile(audio_pipe, audio_data)
                            else:
                                # timestamp = time.time()
                                # audio_queue.put((timestamp, audio_data))
                                audio_pipe.write(audio_data)
                                audio_pipe.flush()
                            if not curtain:
                                break
                else:
                    audio_data = stream.read(
                        chunk, exception_on_overflow=False)
                    if platform.system() == 'Windows':
                        win32file.WriteFile(audio_pipe, audio_data)
                    else:
                        # timestamp = time.time()
                        # audio_queue.put((timestamp, audio_data))
                        audio_pipe.write(audio_data)
                        audio_pipe.flush()

    except Exception as e:
        print(f"Error in microphone_audio_stream: {e}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        if platform.system() == 'Windows':
            win32pipe.DisconnectNamedPipe(audio_pipe)
            win32file.CloseHandle(audio_pipe)
        else:
            audio_pipe.close()


def video_audio_stream(video_path, stop_event):
    command = [
        'ffmpeg',
        '-i', video_path,
        '-vn',  # No video
        '-f', 'wav',
        'pipe:1'
    ]
    audio_process = subprocess.Popen(command, stdout=subprocess.PIPE)

    while not stop_event.is_set():
        audio_data = audio_process.stdout.read(4096)
        if not audio_data:
            break
        audio_queue.put(audio_data)

    audio_process.terminate()


video_pipe_name = r'\\.\pipe\video_pipe' if PLATFORM == 'Windows' else 'video_pipe'
audio_pipe_name = r'\\.\pipe\audio_pipe' if PLATFORM == 'Windows' else 'audio_pipe'

def dummy_reader(pipe_name):
    with open(pipe_name, 'rb') as pipe:
        while True:
            time.sleep(0.1)  

def create_pipe(pipe_name):
    if PLATFORM == 'Windows':
        print(f"Creating pipe: {pipe_name}")
        pipe = win32pipe.CreateNamedPipe(
            pipe_name,
            win32pipe.PIPE_ACCESS_OUTBOUND,
            win32pipe.PIPE_TYPE_BYTE,
            1, 65536, 65536,
            0,
            None
        )
        print(f"Pipe created: {pipe_name}")
        return pipe
    else:
        try:
            if not os.path.exists(pipe_name):
                os.mkfifo(pipe_name)
            #if pipe_name == audio_pipe_name:
            #    dummy_reader_thread= threading.Thread(target=dummy_reader, args=(pipe_name,))
            #    dummy_reader_thread.start()
            return open(pipe_name, 'wb')
        except OSError as oe:
            if oe.errno != errno.EEXIST:
                raise


def connect_pipe(pipe):
    if PLATFORM == 'Windows':
        print(f"Connecting to pipe: {pipe}")
        win32pipe.ConnectNamedPipe(pipe, None)
        print(f"Connected to pipe: {pipe}")


def extract_audio(video_path, audio_path):
    command = [
        'ffmpeg',
        '-y',
        '-i', video_path,
        '-q:a', '0',
        '-map', 'a',
        audio_path]
    subprocess.run(command)


global video_pipe
video_pipe = None
global audio_pipe
audio_pipe = None
# video_pipe = create_pipe(video_pipe_name)
# audio_pipe = create_pipe(audio_pipe_name)


def HomeView(page: Router):
    FACEBOOK_RTMP_URL = "rtmps://live-api-s.facebook.com:443/rtmp/{}"
    YOUTUBE_RTMP_URL = "rtmp://a.rtmp.youtube.com/live2/{}"
    TWITCH_RTMP_URL = "rtmp://live.twitch.tv/app/{}"
    X_RTMP_URL = "rtmp://X.com/"
    global curtain_array
    curtain_array = []
    global video_added
    video_added = False
    global stream_video_added
    stream_video_added = False
    global curtain
    global stream_started
    stream_started = None
    global input_changed
    global current_input
    current_input = 0
    input_changed = False

    def connect_sqlite():
        conn = sqlite3.connect('statics/data.db')
        cursor = conn.cursor()
        return conn, cursor

    def create_table(cursor):
        cursor.execute('''CREATE TABLE IF NOT EXISTS transmisiones
                      (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      plataforma TEXT NOT NULL,
                      key TEXT NOT NULL)''')

    def get_transmisiones(cursor):
        cursor.execute("SELECT * FROM transmisiones")
        return cursor.fetchall()

    def insert_transmision(cursor, plataforma, key):
        cursor.execute(
            "DELETE FROM transmisiones WHERE plataforma = ?", (plataforma,))
        cursor.execute(
            "INSERT INTO transmisiones (plataforma, key) VALUES (?, ?)", (plataforma, key))

    conn, cursor = connect_sqlite()
    create_table(cursor)
    conn.commit()

    def on_change_drop_list(e):
        global input_changed
        global current_input
        input_changed = True
        current_input = e.control.value
        page.update()

    def on_change_server_input(e):
        page.update()

    def on_change_checkbox(e):
        if e.control.label == "Otro":
            custom_server.disabled = not e.control.value
        checkbox_label = e.control.label
        platform_Inputs[platforms.index(
            checkbox_label)].disabled = not e.control.value
        page.update()
    # Cola para los frames en vivo, con tamaño máximo
    frame_queue = queue.Queue(maxsize=100)
    global process
    process = None
    platforms = ["Facebook", "Youtube", "X", "Otro"]
    keys = get_transmisiones(cursor)
    key_dict = {key[1]: key[2] for key in keys}
    platform_drop_list = [ft.Checkbox(
        label=platform, height=45, on_change=on_change_checkbox) for platform in platforms]
    platform_Inputs = [
        ft.TextField(
            label=platform,
            height=60,
            width=230,
            disabled=True,
            value=key_dict.get(platform, "")
        ) for platform in platforms
    ]

    img_live = ft.Image(
        src="statics/images/black.png",
        border_radius=ft.border_radius.all(20)
    )

    img_preview = ft.Image(
        src="statics/images/black.png",
        border_radius=ft.border_radius.all(20)
    )

    custom_server = ft.TextField(
        width=380,
        label="Ruta del servidor",
        on_change=on_change_server_input,
        disabled=True
    )

    def handle_change_start(e):
        slider_status.value = "Sliding"
        page.update()

    def handle_change(e):
        slider_value.value = str(e.control.value)
        page.update()

    def handle_change_end(e):
        slider_status.value = "Finished sliding"
        page.update()

    def on_change_audio_drop_list(e):
        page.update()

    def stream_webcam(container: ft.Container):
        global video_pipe
        # video_pipe = create_pipe(video_pipe_name)
        # print("Pipe creado")
        streams = ""
        i = 1
        for checkbox in platform_drop_list:
            if checkbox.value:
                key = platform_Inputs[platform_drop_list.index(checkbox)].value
                select = f"[f=flv:onfail=ignore]"
                if checkbox.label == "Facebook":
                    streams += f"{select}{FACEBOOK_RTMP_URL.format(key)}|"
                elif checkbox.label == "Youtube":
                    streams += f"{select}{YOUTUBE_RTMP_URL.format(key)}|"
                elif checkbox.label == "X":
                    streams += f"{select}{X_RTMP_URL.format(key)}|"
                elif checkbox.label == "Otro":
                    streams += f"{select}{custom_server.value}{key}|"
                i += 1
        global curtain
        global current_curtain_selected

        global video_thread_started
        global process
        if not video_thread_started:
            video_thread_started = True
            audio_thread = threading.Thread(target=microphone_audio_stream, args=(
                audio_pipe_name, None, current_curtain_selected, int(audio_drop_list.value)))
            audio_thread.start()

        command = [
            'ffmpeg',
            '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-pixel_format', 'bgr24',
            '-s', '1280x720',
            '-framerate', '30',
            '-thread_queue_size', '4096',
            '-i', video_pipe_name,
            '-f', 's16le',
            '-ac', '2',
            '-ar', '44100',
            '-thread_queue_size', '4096',
            '-i', audio_pipe_name,
            '-map', '0:v',
            '-map', '1:a',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'ultrafast',
            '-tune', 'zerolatency',
            '-b:v', '1500k',
            '-maxrate', '4500k',
            '-bufsize', '512k',
            '-pix_fmt', 'yuv420p',
            '-r', '30',
            '-g', '60',
            '-flags', '+global_header',
            '-f', 'tee',
            streams
        ]
        ffmpeg_thread = threading.Thread(
            target=execute_ffmpeg, args=(command,))
        ffmpeg_thread.start()
        
    def execute_ffmpeg(command):
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        return process

    def capture_screen(container: ft.Container):
        youtube_key = "yj7j-fuzw-7370-6956-38t8"
        rtmp_url = "rtmp://a.rtmp.youtube.com/live2/{}"
        ffmpeg_command = [
            'ffmpeg',
            '-f', 'gdigrab',
            '-framerate', '30',
            '-video_size', '1920x1080',
            '-show_region', '1',
            '-i', 'desktop',
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-preset', 'ultrafast',
            '-f', 'flv',
            rtmp_url.format(youtube_key)
        ]
        p = subprocess.Popen(
            ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            p.terminate()
        while True:

            print("Capturando pantalla")
            frame = p.stdout.read(1920 * 1080 * 3)
            if not frame:
                print("El flujo de salida de ffmpeg ha terminado.")

                break

            try:
                frame_np = np.frombuffer(frame, dtype=np.uint8)
                img = cv2.imdecode(frame_np, flags=cv2.IMREAD_COLOR)
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                _, frame_bytes = cv2.imencode('.png', img)
                frame_base64 = base64.b64encode(frame_bytes).decode('utf-8')
                img.src_base64 = frame_base64
                page.update()
            except Exception as e:
                print("Error al procesar el fotograma:", e)
                break
        p.terminate()  # Terminar el proceso ffmpeg

    def on_Iniciar_transmision_click(e):
        global stream_started
        global video_pipe
        global audio_pipe
        global video_pipe_created
        global audio_pipe_created
        thread = None
        for checkbox in platform_drop_list:
            if checkbox.value:
                key = platform_Inputs[platform_drop_list.index(checkbox)].value
                insert_transmision(cursor, checkbox.label, key)
        conn.commit()
        if PLATFORM == 'Windows':
            connect_pipe(video_pipe)
        if drop_list.value == '1' and not stream_started:
            thread = threading.Thread(target=capture_screen, args=(
                content._get_children()[0]._get_children()[0],))
            thread.start()
        elif drop_list.value != '1' and not stream_started:
            thread = threading.Thread(target=stream_webcam, args=(
                content._get_children()[0]._get_children()[0],))
            thread.start()
        if stream_started:
            stream_started = False
            stream_button.text = "Iniciar transmisión"
        else:
            stream_started = True
            stream_button.text = "Detener transmisión"
        stream_button.update()
        if PLATFORM == 'Darwin':
            video_pipe = create_pipe(video_pipe_name)
            video_pipe_created = True
            print("video_pipe creado")
            audio_pipe = create_pipe(audio_pipe_name)
            audio_pipe_created = True
            print("audio_pipe creado")

    global curtain_thread_started
    global video_thread_started
    video_thread_started = False
    curtain_thread_started = False

    def show_curtain(e):
        global curtain
        global curtain_thread_started
        global stream_video_added
        global current_curtain_selected
        global process
        if curtain == False:
            curtain = True
            live_container.content = stream_video
            live_container.update()
            if stream_video_added:
                stream_video.playlist_remove(0)
            stream_video.playlist_add(ft.VideoMedia(current_curtain_selected))
            live_container.update()
            stream_video.jump_to(0)
            live_container.update()
            live_container.content = stream_video
            live_container.update()
            boton_cortina.text = "Detener cortina"
            boton_cortina.update()
            page.update()
            live_container.update()
            stream_video_added = True
            # curtain_button.text = "Detener cortina"
            page.update()
        else:
            curtain = False
            preview_container.content = video
            live_container.content = img_live
            live_container.update()
            preview_container.update()
            # curtain_button.text = "Iniciar cortina"
            boton_cortina.text = "Iniciar cortina"
            boton_cortina.update()
            page.update()

        if not curtain_thread_started and curtain:
            curtain_thread_started = True
            curtain_thread = threading.Thread(target=capture_curtain_video)
            curtain_thread.start()

    def show_file(e: ft.FilePickerResultEvent):
        for file in e.files:
            if file.path.lower().endswith(('.png', '.jpg', '.jpeg')):
                image = ft.Image(src=file.path,
                                 width=200,
                                 height=50,
                                 border_radius=ft.border_radius.all(20))
                gd = ft.GestureDetector(on_tap=click, key=file.path)
                canvas_container = ft.Container(
                    width=200,
                    height=50,
                    content=gd
                )

                cont = ft.Stack(controls=[
                    image,
                    canvas_container  # Add the container instead of the raw canvas
                ], width=200, height=50)

            elif file.path.lower().endswith(('.mp4', '.avi', '.webm')):
                image = ft.Image(src_base64=generate_video_thumbnail(file.path),
                                 width=200,
                                 height=50,

                                 border_radius=ft.border_radius.all(20))
                gd = ft.GestureDetector(on_tap=click, key=file.path)
                canvas_container = ft.Container(
                    width=200,
                    height=50,
                    content=gd
                )

                cont = ft.Stack(controls=[
                    image,
                    canvas_container  # Add the container instead of the raw canvas
                ], width=200, height=50)

            curtain_array.append(cont)
        images = ft.Row(curtain_array)

        curtain_container.content = images
        curtain_container.update()
    global count
    count = 0

    def click(e):
        global current_curtain_selected
        global video_added
        global count
        if not video_added:
            preview_container.content = video
            preview_container.update()
        if video_added:
            preview_container.content = img_preview
            video.playlist_remove(0)
            preview_container.update()
            page.update()
            preview_container.content = video
            preview_container.update()
            page.update()
            # preview_container.update()
            # page.update()

            # preview_container.update()
            # page.update()

        current_curtain_selected = e.control.key
        video.playlist_add(ft.VideoMedia(e.control.key))
        video.jump_to(0)
        video.update()
        page.update()
        print("Video añadido", video.playlist)
        # video.update()
        # video.update()
        # preview_container.update()
        streaming_and_preview_row.update()
        # page.update()
        video_added = True
        extract_audio_thread = threading.Thread(
            target=extract_audio, args=(current_curtain_selected, AUDIO_OUTPUT))
        extract_audio_thread.start()

    def generate_video_thumbnail(file_path):
        cap = cv2.VideoCapture(file_path)
        ret, frame = cap.read()
        cap.release()
        frame = cv2.resize(frame, (200, 200))
        ret, frame_bytes = cv2.imencode('.png', frame)
        frame_base64 = base64.b64encode(frame_bytes).decode('utf-8')
        return frame_base64
    global current_curtain_selected
    current_curtain_selected = ""

    sample_media = []

    def show_devices():
        video_devices = []
        audio_devices = []
        if PLATFORM == 'Windows':
            result = subprocess.run(['ffmpeg', '-f', 'dshow', '-list_devices',
                                     'true', '-i',
                                     'dummy'], capture_output=True, text=True,
                                    encoding='utf-8')
            output = result.stderr
            for line in output.split('\n'):
                match = re.search(r'"(.*?)" \((.*?)\)', line)
                if match:
                    device_name = match.group(1)
                    device_type = match.group(2)
                    if device_type == "video" and device_name not in video_devices:
                        video_devices.append(device_name)
                    elif device_type == "audio" and device_name not in audio_devices:
                        audio_devices.append(device_name)

        elif PLATFORM == 'Darwin':
            result = subprocess.run(['ffmpeg', '-hide_banner',
                                     '-list_devices', 'true',
                                     '-f', 'avfoundation',
                                     '-i', 'dummy'],
                                    capture_output=True, text=True)

            output = result.stderr
            video_flag = False
            audio_flag = False
            for line in output.split('\n'):
                if 'AVFoundation video devices:' in line:
                    video_flag = True
                    audio_flag = False
                elif 'AVFoundation audio devices:' in line:
                    audio_flag = True
                    video_flag = False
                elif video_flag:
                    match = re.search(r'\[\d+\] (.*)', line)
                    if match:
                        device_name = match.group(1)
                        if device_name not in video_devices and not 'Capture screen' in device_name:
                            video_devices.append(device_name)
                elif audio_flag:
                    match = re.search(r'\[\d+\] (.*)', line)
                    if match:
                        device_name = match.group(1)
                        if device_name not in audio_devices:
                            audio_devices.append(device_name)
        return video_devices, audio_devices

    def play_audio(audio_device):
        subprocess.run(['ffplay', '-nodisp', '-autoexit', '-f',
                       'dshow', '-i', f'audio={audio_device}'])

    def capture_curtain_video():
        global curtain_video_cap
        global stream_started
        curtain_video_cap = cv2.VideoCapture(current_curtain_selected)
        curtain_video_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        curtain_video_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        curtain_video_cap.set(cv2.CAP_PROP_FPS, 30)
        curtain_video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        fps = 30
        frame_duration = 1 / fps
        while True:
            start_time = time.time()
            if curtain:
                ret, curtain_frame = curtain_video_cap.read()
                if not ret:
                    curtain_video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                curtain_frame = cv2.resize(curtain_frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
                # curtain_frame = cv2.cvtColor(curtain_frame, cv2.COLOR_BGR2RGB)
                # curtain_frame = cv2.cvtColor(curtain_frame, cv2.COLOR_RGB2BGR)

                if PLATFORM == 'Windows':
                    win32file.WriteFile(video_pipe, curtain_frame.tobytes())
                else:
                    # timestamp = time.time()
                    # video_queue.put((timestamp, curtain_frame.tobytes()))
                    """with open(AUDIO_OUTPUT, 'rb') as f:
                          while curtain:
                              audio_data = f.read(chunk * 2 * channels)

                              if not audio_data:
                                  f.seek(0)
                                  continue
                              if platform.system() == 'Windows':
                                  timestamp = time.time()
                                  audio_queue.put((timestamp, audio_data))
                                  win32file.WriteFile(audio_pipe, audio_data)
                              else:
                                  #timestamp = time.time()
                                  #audio_queue.put((timestamp, audio_data))
                                  video_pipe.write(curtain_frame.tobytes())
                                  video_pipe.flush()
                                  audio_pipe.write(audio_data)
                                  audio_pipe.flush()
                              if not curtain:
                                  break"""
                video_pipe.write(curtain_frame.tobytes())
                video_pipe.flush()
                end_time = time.time()
                elapsed_time = end_time - start_time
                sleep_time = frame_duration - elapsed_time

                if sleep_time > 0:
                    time.sleep(sleep_time)
            if not stream_started:
                break
        curtain_video_cap.release()
        if PLATFORM == 'Windows':
            win32file.CloseHandle(video_pipe)

        # else:
            # video_pipe.close()
            # os.remove(video_pipe_name)

    def capture_camera_video(pipe_name):
        global video_pipe
        global stream_video_cap
        global input_changed
        global current_input
        global video_queue
        global process
        global audio_pipe
        global audio_pipe_created
        global video_pipe_created
        started = True
        pipeline = (
            'decklinkvideosrc mode=1080p30 ! videoconvert ! appsink'
        )



        if PLATFORM == 'Windows':
            stream_video_cap = cv2.VideoCapture(int(0), cv2.CAP_DSHOW)
        else:
            stream_video_cap = cv2.VideoCapture(int(current_input))
        global stream_started
        stream_video_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        stream_video_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        stream_video_cap.set(cv2.CAP_PROP_FPS, 30)
        # if PLATFORM == 'Windows':
        #   win32pipe.ConnectNamedPipe(video_pipe, None)
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16,
                        channels=1,
                        rate=rate,
                        input=True,
                        frames_per_buffer=chunk,
                        )
        fps = 30
        frame_duration = 1 / fps
        try:
            while True:
                start_time = time.time()
                if input_changed:
                    stream_video_cap.release()
                    if PLATFORM == 'Windows':
                        stream_video_cap = cv2.VideoCapture(
                            int(0), cv2.CAP_DSHOW)
                    else:
                        if (current_input == '10'):
                            stream_video_cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                        else:
                            stream_video_cap = cv2.VideoCapture(int(current_input))
                    stream_video_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    stream_video_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    stream_video_cap.set(cv2.CAP_PROP_FPS, 30)
                    input_changed = False
                ret, camera_frame = stream_video_cap.read()
                if not ret:
                    print("No se pudo leer el fotograma de la cámara")

                    break

                camera_frame = cv2.resize(camera_frame, (1280, 720))
                ret, camera_frame_bytes = cv2.imencode('.png', camera_frame)
                camera_frame_base64 = base64.b64encode(
                    camera_frame_bytes).decode('utf-8')

                if curtain and camera_frame_base64:
                    if img_preview:
                        img_preview.src_base64 = camera_frame_base64
                    if preview_container:
                        preview_container.content = img_preview
                        # page.update()
                        streaming_and_preview_row.update()
                        preview_container.update()
                else:
                    if img_live and camera_frame_base64:
                        img_live.src_base64 = camera_frame_base64
                        live_container.content = img_live
                        # page.update()
                        img_live.update()
                    if live_container:
                        live_container.update()
                    # if stream_started:
                        # if stream_started:
                        # time.sleep(10)
                        # stream_started = False
                        # process.stdin.write(camera_frame.tobytes())
                    # if PLATFORM == 'Windows' and stream_started:
                    #   win32file.WriteFile(video_pipe, camera_frame.tobytes())
                    if stream_started and not PLATFORM == 'Windows':
                        try:
                            # timestamp = time.time()
                            # video_queue.put((timestamp, camera_frame.tobytes()))
                            #audio_pipe = create_pipe(pipe_name)
                            #audio_pipe_created = True
                            video_pipe.write(camera_frame.tobytes())
                            video_pipe.flush()
                            #if audio_pipe_created:
                            #  audio_data = stream.read(chunk, exception_on_overflow=False)
                            #  audio_pipe.write(audio_data)
                            #  audio_pipe.flush()
                       
                            
                              # video_queue.put(camera_frame.tobytes())
                        except Exception as e:
                            print(f"Error en capture_camera_video: {e}")
                    end_time = time.time()
                    elapsed_time = end_time - start_time
                    sleep_time = frame_duration - elapsed_time

                    if sleep_time > 0:
                        time.sleep(sleep_time)
                if stream_started == False:
                    print("Transmisión detenida")
                    video_pipe.close()

        except Exception as e:
            print(f"Error en capture_camera_video: {e}")

        finally:
            stream_video_cap.release()
            print("Cámara liberada")
            if PLATFORM == 'Windows':
                win32pipe.DisconnectNamedPipe(video_pipe)
                win32file.CloseHandle(video_pipe)
            if not PLATFORM == 'Windows' and stream_started:
                video_pipe.close()
                # os.remove(pipe_name)
            # frame_queue.put(camera_frame.tobytes())

                # time.sleep(1 / 30.0)  # Control de la velocidad de fotogramas

    def synchronize_and_send():
        global stream_started
        print("Sincronizando y enviando")
        while stream_started:
            if not video_queue.empty() and not audio_queue.empty():
                video_timestamp, video_frame = video_queue.get()
                audio_timestamp, audio_data = audio_queue.get()

                if abs(video_timestamp - audio_timestamp) < 0.1:
                    if platform.system() == 'Windows':
                        win32file.WriteFile(video_pipe, video_frame)
                        win32file.WriteFile(audio_pipe, audio_data)
                    else:
                        video_pipe.write(video_frame)
                        video_pipe.flush()
                        audio_pipe.write(audio_data)
                        audio_pipe.flush()
                else:
                    pass
            time.sleep(0.01)
    # sincronize_thread = threading.Thread(target=synchronize_and_send)
    # sincronize_thread.start()
    def capture_blackmagic():
        global video_pipe
        global stream_video_cap
        global input_changed
        global current_input
        global video_queue
        global process
        global audio_pipe
        global audio_pipe_created
        global video_pipe_created
        started = True
        pipeline = (
            'decklinkvideosrc mode=1080p30 ! '
            'videoconvert ! '
            'appsink'
        )
        if PLATFORM == 'Windows':
            stream_video_cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        else:
            stream_video_cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        global stream_started
        stream_video_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        stream_video_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        stream_video_cap.set(cv2.CAP_PROP_FPS, 30)
        # if PLATFORM == 'Windows':
        #   win32pipe.ConnectNamedPipe(video_pipe, None)
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16,
                        channels=1,
                        rate=rate,
                        input=True,
                        frames_per_buffer=chunk,
                        input_device_index=int(audio_drop_list.value))
        fps = 30
        frame_duration = 1 / fps
        try:
            while True:
                start_time = time.time()
                if input_changed:
                    stream_video_cap.release()
                    if PLATFORM == 'Windows':
                        stream_video_cap = cv2.VideoCapture(
                            int(0), cv2.CAP_DSHOW)
                    else:
                        stream_video_cap = cv2.VideoCapture(int(current_input))
                    stream_video_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    stream_video_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    stream_video_cap.set(cv2.CAP_PROP_FPS, 30)
                    input_changed = False
                ret, camera_frame = stream_video_cap.read()
                if not ret:
                    print("No se pudo leer el fotograma de la cámara")

                    break

                camera_frame = cv2.resize(camera_frame, (1280, 720))
                ret, camera_frame_bytes = cv2.imencode('.png', camera_frame)
                camera_frame_base64 = base64.b64encode(
                    camera_frame_bytes).decode('utf-8')

                if curtain and camera_frame_base64:
                    if img_preview:
                        img_preview.src_base64 = camera_frame_base64
                    if preview_container:
                        preview_container.content = img_preview
                        # page.update()
                        streaming_and_preview_row.update()
                        preview_container.update()
                else:
                    if img_live and camera_frame_base64:
                        img_live.src_base64 = camera_frame_base64
                        live_container.content = img_live
                        # page.update()
                        img_live.update()
                    if live_container:
                        live_container.update()
                    # if stream_started:
                        # if stream_started:
                        # time.sleep(10)
                        # stream_started = False
                        # process.stdin.write(camera_frame.tobytes())
                    # if PLATFORM == 'Windows' and stream_started:
                    #   win32file.WriteFile(video_pipe, camera_frame.tobytes())
                    if stream_started and not PLATFORM == 'Windows':
                        try:
                            # timestamp = time.time()
                            # video_queue.put((timestamp, camera_frame.tobytes()))
                            #audio_pipe = create_pipe(pipe_name)
                            #audio_pipe_created = True
                            video_pipe.write(camera_frame.tobytes())
                            video_pipe.flush()
                            #if audio_pipe_created:
                            #  audio_data = stream.read(chunk, exception_on_overflow=False)
                            #  audio_pipe.write(audio_data)
                            #  audio_pipe.flush()
                       
                            
                              # video_queue.put(camera_frame.tobytes())
                        except Exception as e:
                            print(f"Error en capture_camera_video: {e}")
                    end_time = time.time()
                    elapsed_time = end_time - start_time
                    sleep_time = frame_duration - elapsed_time

                    if sleep_time > 0:
                        time.sleep(sleep_time)
                if stream_started == False:
                    print("Transmisión detenida")
                    video_pipe.close()

        except Exception as e:
            print(f"Error en capture_camera_video: {e}")

        finally:
            stream_video_cap.release()
            print("Cámara liberada")
            if PLATFORM == 'Windows':
                win32pipe.DisconnectNamedPipe(video_pipe)
                win32file.CloseHandle(video_pipe)
            if not PLATFORM == 'Windows' and stream_started:
                video_pipe.close()
                # os.remove(pipe_name)
            # frame_queue.put(camera_frame.tobytes())

                # time.sleep(1 / 30.0)  # Control de la velocidad de fotogramas
  
    
    video_devices, audio_devices = show_devices()

    options = [ft.dropdown.Option('1', "Grabar pantalla")] + [ft.dropdown.Option(
        key=str(i), text=device) for i, device in enumerate(video_devices)]
    options.append(ft.dropdown.Option('10', "Blackmagic"))
    drop_list = ft.Dropdown(
        width=380,
        options=options,
        on_change=on_change_drop_list,
        label="Dispositivo de video"
    )
    audio_options = [ft.dropdown.Option('1', "Grabar audio de la pantalla")] + [
        ft.dropdown.Option(device, device) for i, device in enumerate(audio_devices)]
    audio_drop_list = ft.Dropdown(
        width=380,
        options=audio_options,
        on_change=on_change_audio_drop_list,
        label="Dispositivo de audio"
    )
    pick_files_dialog = ft.FilePicker(on_result=show_file)
    selected_files = ft.Text()
    page.set_overlay(pick_files_dialog)
    # page.page.overlay.append(pick_files_dialog)
    curtain_container = ft.Container(
        bgcolor=ft.colors.BLACK,
        width=1300,
        height=50,
    )
    live_container = ft.Container(
        bgcolor=ft.colors.BLACK,
        width=700,
        height=350,
        content=img_live
    )
    video = ft.Video(
        expand=True,
        playlist=sample_media,
        playlist_mode=ft.PlaylistMode.LOOP,
        fill_color=ft.colors.BLACK26,
        aspect_ratio=16/9,
        volume=100,
        autoplay=True,
        filter_quality=ft.FilterQuality.HIGH,
        muted=False,
        on_loaded=lambda e: print("Video cargado exitosamente!"),
        on_enter_fullscreen=lambda e: print("Video a pantalla completa!"),
        on_exit_fullscreen=lambda e: print(
            "Video salió de pantalla completa!"),
    )
    stream_video = ft.Video(
        expand=True,
        playlist=sample_media,
        playlist_mode=ft.PlaylistMode.LOOP,
        fill_color=ft.colors.BLACK26,
        aspect_ratio=16/9,
        volume=100,
        autoplay=True,
        filter_quality=ft.FilterQuality.HIGH,
        muted=False,
        show_controls=False,
        on_loaded=lambda e: print("Video cargado exitosamente!"),
        on_enter_fullscreen=lambda e: print("Video a pantalla completa!"),
        on_exit_fullscreen=lambda e: print(
            "Video salió de pantalla completa!"),
    )

    preview_container = ft.Container(
        bgcolor=ft.colors.BLACK,
        width=700,
        height=350,
    )

    streaming_and_preview_row = ft.Row(
        [
            preview_container,
            live_container
        ],
        alignment=ft.MainAxisAlignment
    )
    boton_cortina = ft.TextButton(
        text="Iniciar cortina",
        width=400,
        on_click=show_curtain
    )

    stream_button = ft.TextButton(
        text="Iniciar transmisión",
        width=400,
        on_click=on_Iniciar_transmision_click
    )
    camera_thread = threading.Thread(
        target=capture_camera_video, args=(video_pipe_name,))
    camera_thread.start()
    content = ft.Column(
        [
            streaming_and_preview_row,
            ft.Row(
                [
                    curtain_container
                ],
                alignment=ft.MainAxisAlignment.CENTER
            ),
            ft.ResponsiveRow(
                [
                    ft.Card(
                        content=ft.Container(
                            content=ft.Column([
                                ft.ListTile(
                                    title=ft.Text("Escenas"),
                                    bgcolor=ft.colors.BLACK38,
                                ),
                                ft.Column([
                                    ft.TextButton(
                                        "Escena"
                                    ),
                                    ft.TextButton(
                                        "Empezando"
                                    ),
                                    ft.TextButton(
                                        "Comerciales"
                                    ),
                                    ft.ElevatedButton(
                                        "Seleccionar video",
                                        icon=ft.icons.UPLOAD_FILE,
                                        on_click=lambda _: pick_files_dialog.pick_files(
                                            allow_multiple=True,
                                            allowed_extensions=[
                                                "mp4", "avi"]
                                        ),
                                    ),
                                    selected_files,
                                    custom_server
                                ])
                            ]),
                            border_radius=10,
                        ),
                        col={"sm": 6, "md": 4, "xl": 3},
                    ),
                    ft.Card(
                        content=ft.Container(
                            content=ft.Column([
                                ft.ListTile(
                                    title=ft.Text(
                                        "Configuración de transmisión"),
                                    bgcolor=ft.colors.BLACK38,
                                ),
                                ft.Column([
                                    ft.Row([
                                        ft.Column(platform_drop_list),
                                        ft.Column(platform_Inputs),
                                    ])
                                ],
                                    scroll=True)
                            ]),
                            border_radius=10,
                        ),
                        col={"sm": 6, "md": 4, "xl": 3},
                    ),
                    ft.Card(
                        content=ft.Container(
                            content=ft.Column([
                                ft.ListTile(
                                    title=ft.Text("Audio y Video"),
                                    bgcolor=ft.colors.BLACK38,
                                ),
                                ft.Column([drop_list,
                                           audio_drop_list,
                                           #   ft.Container(
                                           #     content=ft.CupertinoSlider(
                                           #               divisions=100,
                                           #               max=100,
                                           #               width=400,
                                           #               active_color=ft.colors.BLUE,
                                           #               thumb_color=ft.colors.BLUE,
                                           #               on_change_start=handle_change_start,
                                           #               on_change_end=handle_change_end,
                                           #               on_change=handle_change,
                                           #             )
                                           #   )
                                           # ,

                                           ]),
                            ]),
                            border_radius=10,
                        ),
                        col={"sm": 6, "md": 4, "xl": 3},
                    ),
                    ft.Card(
                        content=ft.Container(
                            content=ft.Column([
                                ft.ListTile(
                                    title=ft.Text("Controles"),
                                    bgcolor=ft.colors.BLACK38,
                                ),
                                ft.Column([
                                    stream_button,
                                    ft.TextButton(
                                        "Iniciar grabación",
                                        width=400
                                    ),
                                    boton_cortina
                                ])
                            ])
                        ),
                        col={"sm": 6, "md": 4, "xl": 3},
                    ),

                ],
                alignment=ft.MainAxisAlignment.CENTER,
            )
        ]
    )

    return content


# MAKE 7 - 7 SECUENCE UNTIL 177
