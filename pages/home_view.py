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
# import gi
# gi.require_version('Gst', '1.0')
# from gi.repository import Gst, GLib
import cv2
import threading
import time
import os
from logger.logs import log
from numba import njit
from utils.numbaresize import resize_numba
import sys
temp_video_file = 'temp_video.ts'

if os.path.exists(temp_video_file):
    os.remove(temp_video_file)
def get_ffmpeg_path():
    if hasattr(sys, '_MEIPASS'):
        ffmpeg_path = os.path.join(sys._MEIPASS, 'ffmpeg.exe')  
    else:
        ffmpeg_path = 'ffmpeg'

    return ffmpeg_path

def get_audio_backend():
    if PLATFORM == 'Windows':
        return 'dshow'
    elif PLATFORM == 'Darwin':
        return 'avfoundation'
global gstreamer_pipeline
gstreamer_pipeline = None
global selector_live
selector_live = None
global selector_video
selector_video = None
ffmpeg = True
PLATFORM = platform.system()
AUDIO_OUTPUT = "statics/audio_output.wav"
VIDEO_OUTPUT = "statics/video_output.mp4"
# if PLATFORM == 'Windows':
#     import win32pipe
#     import win32file

channels = 2
rate = 48000
chunk = 1024
# if PLATFORM == 'Windows':
#     import os, msvcrt
#     if sys.stdout is None:
#         sys.stdout = open(os.devnull, 'w')
#     if sys.stderr is None:
#         sys.stderr = open(os.devnull, 'w')

#     if sys.stdout and hasattr(sys.stdout, 'fileno'):
#         msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
#         msvcrt.setmode(sys.stderr.fileno(), os.O_BINARY)

global curtain
curtain = False
global audio_pipe_created
audio_pipe_created = False
global video_pipe_created
video_pipe_created = False
audio_process = None
audio_queue = queue.Queue()
video_queue = queue.Queue()

video_queue_gui = queue.Queue()
global cortinillas
cortinillas = False
global started_with_curtain
started_with_curtain = True
global frame
frame = None
global camera_frame
camera_frame = None
def microphone_audio_stream(pipe_name, event, current_curtain_selected, audio_device_index=None):
    global audio_pipe
    global curtain
    global audio_pipe_created
    global current_input
    global selected_channels
    global selected_rate
    global selected_buffer
    audio_pipe = create_pipe(pipe_name)
    audio_pipe_created = True
    audio_device_index = None
    selected_channels = 2  
    selected_rate = 44100  
    selected_buffer = 1024  
    p = pyaudio.PyAudio()
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=selected_channels,
            rate=selected_rate,
            input=True,
            frames_per_buffer=selected_buffer,
            input_device_index=audio_device_index
        )
    except Exception as e:
        log.exception(f"Error al abrir el dispositivo de audio: {e}")
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
                            audio_data = f.read(selected_buffer * 2 * selected_channels)
                            if not audio_data:
                                f.seek(0)
                                continue
                            if platform.system() == 'Windows':
                                timestamp = time.time()
                                audio_queue.put((timestamp, audio_data))
                                win32file.WriteFile(audio_pipe, audio_data)
                            else:
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
                        audio_pipe.write(audio_data)
                        audio_pipe.flush()

    except Exception as e:
        log.exception(f"Error en el hilo de audio: {e}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        if platform.system() == 'Windows':
            win32pipe.DisconnectNamedPipe(audio_pipe)
            win32file.CloseHandle(audio_pipe)
        else:
            audio_pipe.close()

video_pipe_name = r'\\.\pipe\video_pipe' if PLATFORM == 'Windows' else 'video_pipe'
audio_pipe_name = r'\\.\pipe\audio_pipe' if PLATFORM == 'Windows' else 'audio_pipe'

def dummy_reader(pipe_name):
    with open(pipe_name, 'rb') as pipe:
        while True:
            time.sleep(0.1)  
def send_to_ffmpeg():
    global process
    while True:
        if not video_queue.empty():
            frame = video_queue.get(block=False)
            process.stdin.write(frame)
            process.stdin.flush()
def create_pipe(pipe_name):
    if PLATFORM == 'Windows':
        pipe = win32pipe.CreateNamedPipe(
            pipe_name,
            win32pipe.PIPE_ACCESS_OUTBOUND,
            win32pipe.PIPE_TYPE_BYTE,
            1, 65536, 65536,
            0,
            None
        )
        return pipe
    else:
        try:
            if not os.path.exists(pipe_name):
                os.mkfifo(pipe_name)
            return open(pipe_name, 'wb')
        except OSError as oe:
            if oe.errno != errno.EEXIST:
                raise


def connect_pipe(pipe):
    if PLATFORM == 'Windows':
        win32pipe.ConnectNamedPipe(pipe, None)




def extract_audio(video_path, audio_path, video_output, width, height):
    command = [
        get_ffmpeg_path(),
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
def adaptive_resize(frame, target_width, target_height, performance_mode=False):
    original_height, original_width = frame.shape[:2]

    aspect_ratio_original = original_width / original_height
    aspect_ratio_target = target_width / target_height

    if aspect_ratio_original > aspect_ratio_target:
        new_width = target_width
        new_height = int(target_width / aspect_ratio_original)
    else:
        new_height = target_height
        new_width = int(target_height * aspect_ratio_original)

    if performance_mode:
        resized_frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_NEAREST)
    else:
        resized_frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)

    final_frame = np.zeros((target_height, target_width, 3), dtype=np.uint8)

    x_offset = (target_width - new_width) // 2
    y_offset = (target_height - new_height) // 2

    final_frame[y_offset:y_offset+new_height, x_offset:x_offset+new_width] = resized_frame

    return final_frame

# Gst.init(None)
def start_gstreamer_pipeline():
    pipeline_str = (
    "input-selector name=selector_live ! "
    "videoconvert ! "
    "videoscale ! "
    "video/x-raw,width=1280,height=720,format=I420 ! "
    "x264enc bitrate=1500 speed-preset=ultrafast tune=zerolatency ! "
    "flvmux name=mux ! rtmpsink location='rtmp://a.rtmp.youtube.com/live2/your_stream_key'"
)

    pipeline = Gst.parse_launch(pipeline_str)
    
    if not pipeline:
        log.exception("Error al crear el pipeline de GStreamer")
    else:
        log.exception("Pipeline de GStreamer creado con éxito")
    
    return pipeline

def configure_sources(pipeline, selector_live, selector_video, source_type, source_value=None):
    if source_type == "file":
        file_source = Gst.parse_bin_from_description(
            f"filesrc location=\"{source_value}\" ! decodebin ! videoconvert ! queue", True)
        pipeline.add(file_source)
        file_source.get_static_pad("src").link(selector_video.get_request_pad("sink_%u"))
    elif source_type == "decklink":
        live_source = Gst.parse_bin_from_description(
            "decklinkvideosrc mode=auto connection=1 ! videoconvert ! queue", True)
        pipeline.add(live_source)
        live_source.get_static_pad("src").link(selector_live.get_request_pad("sink_%u"))
    elif source_type == "camera":
        live_source = Gst.parse_bin_from_description(
            f"v4l2src device={source_value} ! videoconvert ! queue", True)
        pipeline.add(live_source)
        live_source.get_static_pad("src").link(selector_live.get_request_pad("sink_%u"))
    else:
        raise ValueError("Tipo de fuente no soportado.")

def switch_input_selector(selector, pad_name):
    pad = selector.get_static_pad(pad_name)
    if pad:
        selector.set_property("active-pad", pad)

@njit
def convert_to_frame(raw_frame, width, height):
    return np.frombuffer(raw_frame, np.uint8).reshape((height, width, 3))

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
    current_input = None
    input_changed = False
    global current_audio_input
    current_audio_input = None
    global height
    global width
    height = 720
    width = 1280
    global curtain_video_cap
    curtain_video_cap = None
    def connect_sqlite():
        if not os.path.exists('statics'):
            os.makedirs('statics')
        if not os.path.exists('statics/data.db'):
            open('statics/data.db', 'w').close()
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
        global current_audio_input
        global selected_channels
        global selected_rate
        device_name = audio_drop_list.options[int(e.control.value)].text
        p = pyaudio.PyAudio()
        for i in range(p.get_device_count()):
            if p.get_device_info_by_index(i).get('name') == device_name:
                selected_channels = p.get_device_info_by_index(i).get('maxInputChannels')
                selected_rate = int(p.get_device_info_by_index(i).get('defaultSampleRate'))

        current_audio_input = e.control.value

        page.update()

    def stream_webcam(container: ft.Container):
        global video_pipe
        global current_audio_input
        global height
        global width
        global selected_channels
        global selected_rate
        global selected_buffer
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
        if not video_thread_started and cortinillas:
            video_thread_started = True
            audio_thread = threading.Thread(target=microphone_audio_stream, args=(
                audio_pipe_name, None, current_curtain_selected, 0))
            audio_thread.start()
        if not cortinillas:
            video_pipe_name = 'video='+ drop_list.options[int(current_input)].text if current_input != '10' else 'video=' +drop_list.options[int(1)].text
            audio_pipe_name = 'audio='+audio_drop_list.options[int(current_audio_input)].text
            print(video_pipe_name + " " + audio_pipe_name)

            command = [
                get_ffmpeg_path(),
                '-y',
                '-f', 'image2pipe',
                '-vcodec', 'mjpeg',
                # '-framerate', '30',  # Set desired input framerate
                '-i', '-',  # Read encoded frames from stdin
                '-f', get_audio_backend(),
                '-thread_queue_size', '16384',
                '-rtbufsize', '100M',
                '-i', f'audio={audio_drop_list.options[int(current_audio_input)].text}',
                '-map', '0:v',
                '-map', '1:a',
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-b:v', '3500k',
                '-maxrate', '6000k',
                '-bufsize', '12000k',
                '-b:a', '96k',
                '-pix_fmt', 'yuv420p',
                '-g', '60',
                '-vsync', 'cfr',  # Use constant frame rate sync to avoid conflicts
                '-async', '1',
                '-use_wallclock_as_timestamps', '1',
                '-copyts',
                '-start_at_zero',
                '-vf', 'setpts=PTS+2/TB',  # Add 2-second delay to the video
                '-f', 'tee',
                streams
            ]



        else:
            command = [
            get_ffmpeg_path(),
            '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-pixel_format', 'bgr24',
            '-s', f'{width}x{height}',
            '-framerate', '30',
            '-thread_queue_size', '4096',
            '-i', video_pipe_name,
            '-f', 's16le',
            '-ac', f'{selected_channels}',
            '-ar', f'{selected_rate}',
            '-thread_queue_size', '4096',
            '-i', audio_pipe_name,
            '-map', '0:v',
            '-map', '1:a',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'veryfast',
            '-tune', 'zerolatency',
            '-b:v', '6800k', 
            '-maxrate', '6800k', 
            '-bufsize', '13600k',  
            '-b:a', '128k',
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
        global frame
        global width    
        global height
        global stream_video_cap
        global started_with_curtain
        global process
        try:
            if PLATFORM == 'Windows':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW


                process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                                    startupinfo=startupinfo
                                                    )
            elif PLATFORM == 'Darwin':  
                process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        stdout=subprocess.PIPE,  
                                        text=True) 

            def read_stderr():
                while True:
                    line = process.stderr.readline().decode('utf-8')
                    if line:
                        handle_ffmpeg_error(line.strip())
                    else:
                        break

            stderr_thread = threading.Thread(target=read_stderr)
            stderr_thread.start()

            process.wait()
            stderr_thread.join()
        except Exception as e:
            handle_ffmpeg_error(f"Error al ejecutar FFmpeg: {str(e)}")


    def handle_ffmpeg_error(error_message):
        lower_message = error_message.lower()
        
        error_keywords = ["error", "failed", "broken pipe", "cannot"]
        if any(keyword in lower_message for keyword in error_keywords):
            if "slave" in lower_message:
                error_message = "No se ha podido comenzar el stream en alguno de los servidores seleccionados."
            if "broken pipe" in lower_message:
                error_message = "Se ha perdido la conexión con el servidor."
            log.error(f"Error en FFmpeg: {error_message}")
            show_error_alert(error_message)
    def show_error_alert(error_message):
        alert = ft.AlertDialog(
            modal=True,
            title=ft.Text("Error"),
            content=ft.Text(error_message),
            actions=[
                ft.TextButton("Cerrar", on_click=lambda e: page.close_dialog())
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            open=True
        )
        page.set_dialog(alert)
        page.update()

    def capture_screen(container: ft.Container):
        youtube_key = "yj7j-fuzw-7370-6956-38t8"
        rtmp_url = "rtmp://a.rtmp.youtube.com/live2/{}"
        ffmpeg_command = [
            get_ffmpeg_path(),
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

            frame = p.stdout.read(1920 * 1080 * 3)
            if not frame:
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
                log.exception(f"Error al decodificar el fotograma: {e}")
                break
        p.terminate()  

    def on_Iniciar_transmision_click(e):
        global stream_started
        global video_pipe
        global audio_pipe
        global video_pipe_created
        global audio_pipe_created
        global cortinillas
        thread = None
        if cortinillas and PLATFORM == 'Windows':
            alert = ft.AlertDialog(
                modal=True,
                title=ft.Text("Error"),
                content=ft.Text("Las cortinillas no están disponibles en Windows"),
                actions=[
                    ft.TextButton("Cerrar", on_click=lambda e: page.close_dialog())
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                open=True
            )
            page.set_dialog(alert)
            return     
        if not current_audio_input or not current_input:
            alert = ft.AlertDialog(
                modal=True,
                title=ft.Text("Error"),
                content=ft.Text("Seleccione un dispositivo de audio y video"),
                actions=[
                    ft.TextButton("Cerrar", on_click=lambda e: page.close_dialog())
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                open=True
            )
            page.set_dialog(alert)
            return
        checkbox_values = [checkbox.value for checkbox in platform_drop_list]
        if not any(checkbox_values):
            alert = ft.AlertDialog(
                modal=True,
                title=ft.Text("Error"),
                content=ft.Text("Seleccione al menos una plataforma"),
                actions=[
                    ft.TextButton("Cerrar", on_click=lambda e: page.close_dialog())
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                open=True
            )
            page.set_dialog(alert)
            return
        if not stream_started:
            for checkbox in platform_drop_list:
                if checkbox.value:
                    key = platform_Inputs[platform_drop_list.index(checkbox)].value
                    insert_transmision(cursor, checkbox.label, key)
            conn.commit()
            if PLATFORM == 'Windows' and cortinillas == True:
                connect_pipe(video_pipe)
            # if drop_list.value == '1' and not stream_started:
            #     thread = threading.Thread(target=capture_screen, args=(
            #         content._get_children()[0]._get_children()[0],))
            #     thread.start()
            if not stream_started and ffmpeg:
                thread = threading.Thread(target=stream_webcam, args=(
                    content._get_children()[0]._get_children()[0],))
                thread.start()
            elif not stream_started and not ffmpeg:
                thread = threading.Thread(target=manage_Gstreamer)
                thread.start()
            stream_started = True
            stream_button.text = "Detener transmisión"
            if PLATFORM == 'Darwin':
                video_pipe = create_pipe(video_pipe_name)
                video_pipe_created = True
                audio_pipe = create_pipe(audio_pipe_name)
                audio_pipe_created = True
        else:
            stream_started = False
            stream_button.text = "Iniciar transmisión"
            if PLATFORM == 'Darwin':
                video_pipe.close()
                audio_pipe.close()
            process.terminate()
            
        stream_button.update()

        audio_drop_list.disabled = stream_started
        drop_list.disabled = stream_started
        for checkbox in platform_drop_list:
            checkbox.disabled = stream_started
        page.update()


    global curtain_thread_started
    global video_thread_started
    video_thread_started = False
    curtain_thread_started = False
    def manage_Gstreamer():
        global gstreamer_pipeline, selector_live, selector_video
        gstreamer_pipeline = start_gstreamer_pipeline()
        selector_live = gstreamer_pipeline.get_by_name("selector_live")
        selector_video = gstreamer_pipeline.get_by_name("selector_video")
        gstreamer_pipeline.set_state(Gst.State.PLAYING)

        def on_source_selected(source_type, source_value=None):
            configure_sources(gstreamer_pipeline, selector_live, selector_video, source_type, source_value)

        on_source_selected("decklink")
        loop = GLib.MainLoop()
        try:
            loop.run()
        except Exception as e:
            log.exception(f"Error en el bucle principal de GStreamer: {e}")
        finally:
            gstreamer_pipeline.set_state(Gst.State.NULL)

        # Limpiar
        pipeline.set_state(Gst.State.NULL)
    def show_curtain(e):
        global curtain
        global curtain_thread_started
        global stream_video_added
        global current_curtain_selected
        global process
        global curtain_video_cap  
        global gstreamer_pipeline
        global selector_video
        global selector_live
        if PLATFORM == 'Windows':
            alert = ft.AlertDialog(
                modal=True,
                title=ft.Text("Error"),
                content=ft.Text("Las cortinillas no están disponibles en Windows"),
                actions=[
                    ft.TextButton("Cerrar", on_click=lambda e: page.close_dialog())
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                open=True
            )
            page.set_dialog(alert)
            return
        if not current_curtain_selected:
            alert = ft.AlertDialog(
                modal=True,
                title=ft.Text("Error"),
                content=ft.Text("Seleccione una cortinilla"),
                actions=[
                    ft.TextButton("Cerrar", on_click=lambda e: page.close_dialog())
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                open=True
            )
            page.set_dialog(alert)
            return
        if curtain == False:
            curtain = True
            live_container.content = stream_video
            live_container.update()

            if stream_video_added:
                stream_video.playlist_remove(0)
            
            stream_video.playlist_add(ft.VideoMedia(current_curtain_selected))
            stream_video.jump_to(0)
            live_container.update()
            boton_cortina.text = "Detener cortina"
            boton_cortina.update()
            stream_video_added = True
            if not ffmpeg and gstreamer_pipeline is not None:
                switch_input_selector(selector_video, "sink_%u")
            if curtain_video_cap is not None:
                curtain_video_cap.release()  
            curtain_video_cap = cv2.VideoCapture(current_curtain_selected) 
            
            if not curtain_thread_started:
                curtain_thread_started = True
                curtain_thread = threading.Thread(target=capture_curtain_video)
                curtain_thread.start()

        else:
            curtain = False
            preview_container.content = video
            live_container.content = img_live
            live_container.update()
            preview_container.update()
            boton_cortina.text = "Iniciar cortina"
            boton_cortina.update()
            if not ffmpeg and gstreamer_pipeline is not None:
                switch_input_selector(selector_live, "sink_%u")
            
            if curtain_video_cap is not None:
                curtain_video_cap.release()
                curtain_video_cap = None
                curtain_thread_started = False  

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
                    canvas_container  
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
                    canvas_container  
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
        global height
        global width
        global gstreamer_pipeline, selector_video, selector_live

        if not video_added:
            preview_container.content = video
            preview_container.update()
        if video_added:
            preview_container.content = img_preview
            video.playlist_remove(0)
            preview_container.update()
            preview_container.content = video
            preview_container.update()
        current_curtain_selected = e.control.key
        if not ffmpeg and gstreamer_pipeline is not None:
            configure_sources(gstreamer_pipeline, selector_live, selector_video, "file", current_curtain_selected)
        video.playlist_add(ft.VideoMedia(e.control.key))
        video.jump_to(0)
        video.update()
        streaming_and_preview_row.update()
        video_added = True
        extract_audio_thread = threading.Thread(
            target=extract_audio, args=(current_curtain_selected, AUDIO_OUTPUT, VIDEO_OUTPUT, width, height))
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
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run([get_ffmpeg_path(), '-f', get_audio_backend(), '-list_devices',
                                     'true', '-i',
                                     'dummy'], capture_output=True, text=True,
                                    encoding='utf-8',
                                        startupinfo=startupinfo
                        )
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
            result = subprocess.run([get_ffmpeg_path(), '-hide_banner',
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

    def capture_curtain_video():
        global curtain_video_cap
        global stream_started
        global height
        global width
        global ffmpeg
        fps = 30
        frame_duration = 1 / fps
        target_width, target_height = width, height
        while curtain:
            start_time = time.time()
            ret, curtain_frame = curtain_video_cap.read()
            if not ret:
                curtain_video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  
                continue

            curtain_frame = adaptive_resize(curtain_frame, target_width, target_height, performance_mode=True)
            if ffmpeg:
                if PLATFORM == 'Windows':
                    win32file.WriteFile(video_pipe, curtain_frame.tobytes())
                else:
                    video_pipe.write(curtain_frame.tobytes())
                    video_pipe.flush()

            end_time = time.time()
            elapsed_time = end_time - start_time
            sleep_time = frame_duration - elapsed_time

            # if sleep_time > 0:
            #     time.sleep(sleep_time)
        if curtain_video_cap is not None:
            curtain_video_cap.release()
        curtain_video_cap = None

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
        global height
        global width
        global ffmpeg
        global frame
        global started_with_curtain
        global camera_frame
        started = True
        pipeline = 'decklinkvideosrc mode=auto connection=1 ! videoconvert ! appsink'
        try:
            if PLATFORM == 'Windows':
                stream_video_cap = cv2.VideoCapture(int(0))
            else:
                stream_video_cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        except Exception as e:
            log.exception(f"Error al abrir el pipeline GStreamer: {e}")
        global stream_started
        if not stream_video_cap.isOpened():
            log.error("No se pudo abrir el dispositivo de video")
        fps = 30
        frame_duration = 1 / fps
        p = pyaudio.PyAudio()
        p.open(
            format=pyaudio.paInt16,
            channels=2,
            rate=rate,
            input=True,
            frames_per_buffer=chunk
        )
        used_input = current_input

        count = 0
        start_time = time.time()
        stream_video_cap.set(cv2.CAP_PROP_FPS, 30)

        try:
            while True:
                count += 1
                if not stream_video_cap.isOpened():
                    continue
                
                # start_time = time.time()
                if input_changed and current_input != used_input:
                    used_input = current_input
                    stream_video_cap.release()
                    if PLATFORM == 'Windows':
                        if current_input == '10':
                            stream_video_cap = cv2.VideoCapture(
                                int(0))
                        else:
                            stream_video_cap = cv2.VideoCapture(
                                int(current_input))
                    else:
                        if (current_input == '10'):
                            stream_video_cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                        else:
                            stream_video_cap = cv2.VideoCapture(int(current_input), cv2.CAP_GSTREAMER)
                    input_changed = False
                ret, camera_frame = stream_video_cap.read()

                if not ret:
                    log.info("No se pudo leer el fotograma de la cámara")
                    continue
                
                video_queue_gui.put(camera_frame, block=False)
                if not curtain:
                    if stream_started and not PLATFORM == 'Windows' and ffmpeg:
                        try:
                            video_pipe.write(camera_frame.tobytes())
                            video_pipe.flush()
                        except Exception as e:
                            pass
                    elif stream_started and PLATFORM == 'Windows' and ffmpeg:
                        try:
                            success, encoded_image = cv2.imencode('.jpg', camera_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])

                            process.stdin.write(encoded_image.tobytes())
                        except Exception as e:
                            pass
                    end_time = time.time()  
                    elapsed_time = end_time - start_time
                    sleep_time = frame_duration - elapsed_time
                    # if sleep_time > 0:
                    #     time.sleep(sleep_time)
                if stream_started == False:
                    if PLATFORM == 'Darwin':
                        video_pipe.close()

        except Exception as e:
            log.exception(f"Error en el hilo de la cámara: {e}")

        finally:
            stream_video_cap.release()
            if not PLATFORM == 'Windows' and stream_started:
                video_pipe.close()
        
            return
    def update_GUI():
        global curtain
        global height
        global width
        while True:
            if not video_queue_gui.empty():
                camera_frame = video_queue_gui.get(block=False)
                height, width, _ = camera_frame.shape if camera_frame is not None else (720, 1280, 3)

                camera_frame2 = adaptive_resize(camera_frame, width, height, True)
                ret, camera_frame_bytes = cv2.imencode('.png', camera_frame2)
                camera_frame_base64 = base64.b64encode(
                    camera_frame_bytes).decode('utf-8')
                if curtain and camera_frame_base64:
                            if img_preview:
                                img_preview.src_base64 = camera_frame_base64
                            if preview_container:
                                preview_container.content = img_preview
                                streaming_and_preview_row.update()
                                preview_container.update()
                else:
                    if img_live and camera_frame_base64:
                        img_live.src_base64 = camera_frame_base64
                        live_container.content = img_live
                        img_live.update()
                    if live_container:
                        live_container.update()

    update_gui_thread = threading.Thread(target=update_GUI)
    update_gui_thread.start()

    video_devices, audio_devices = show_devices()

    options = [ft.dropdown.Option(
        key=str(i), text=device) for i, device in enumerate(video_devices)]
    options.append(ft.dropdown.Option('10', "Blackmagic"))
    drop_list = ft.Dropdown(
        width=380,
        options=options,
        on_change=on_change_drop_list,
        label="Dispositivo de video"
    )
    audio_options = [
        ft.dropdown.Option(
            key=str(i), text=device) for i, device in enumerate(audio_devices)]
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

    def on_change_switch(e):
        global cortinillas
        cortinillas = e.control.value
        print(cortinillas)
        page.update()
    cortinillas_selection = ft.Switch(
        label="Transmision con cortinillas",
        value=False,
        on_change=on_change_switch,
        label_style=ft.TextStyle.decoration_color
    )

    camera_thread = threading.Thread(
        target=capture_camera_video, args=(video_pipe_name,))
    camera_thread.start()

    page.set_on_close(lambda: camera_thread.terminate())
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
                                    cortinillas_selection,
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
                                           audio_drop_list
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


