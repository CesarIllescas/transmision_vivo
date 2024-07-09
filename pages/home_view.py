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

print("Starting HomeView")
PLATFORM = platform.system()
AUDIO_OUTPUT = "statics/audio_output.wav"
if PLATFORM == 'Windows':
  import win32pipe, win32file
  
channels = 2
rate = 44100
chunk = 1024

    
global curtain
curtain = False
  
audio_process = None
audio_queue = queue.Queue()

def microphone_audio_stream(pipe_name, event, current_curtain_selected):
    print("Starting audio capture")
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=chunk)
    pipe = create_pipe(pipe_name)
    if platform.system() == 'Windows':
        connect_pipe(pipe)
    event.set()  # Signal that the audio pipe is ready
    try:
        while True:
            audio_data = stream.read(chunk)
            if platform.system() == 'Windows':
                win32file.WriteFile(pipe, audio_data)
            else:
                if curtain:
                  with open(AUDIO_OUTPUT, 'rb') as f:
                    audio_data = f.read()
                else:
                  audio_data = stream.read(chunk)
                pipe.write(audio_data)
                pipe.flush()
    except Exception as e:
        print(f"Error in microphone_audio_stream: {e}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        if platform.system() == 'Windows':
            win32pipe.DisconnectNamedPipe(pipe)
            win32file.CloseHandle(pipe)
        else:
            pipe.close()
            os.remove(pipe_name)

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
        if not os.path.exists(pipe_name):
            print(f"Creating FIFO: {pipe_name}")
            os.mkfifo(pipe_name)
        return open(pipe_name, 'wb')

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
#video_pipe = create_pipe(video_pipe_name)
#audio_pipe = create_pipe(audio_pipe_name)  

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
    stream_started = False
    def on_change_drop_list(e):
      print(audio_drop_list.value)
      
      page.update()

    def on_change_server_input(e):
      page.update()
    def on_change_checkbox(e):
      if e.control.label == "Otro":
         custom_server.disabled = not e.control.value
      checkbox_label = e.control.label
      platform_Inputs[platforms.index(checkbox_label)].disabled = not e.control.value
      page.update()
    frame_queue = queue.Queue(maxsize=100)  # Cola para los frames en vivo, con tamaño máximo
    global process 
    process = None
    platforms = ["Facebook", "Youtube", "X", "Otro"]
    platform_drop_list = [ft.Checkbox(label=platform, height=45, on_change=on_change_checkbox) for platform in platforms]
    platform_Inputs = [ft.TextField(label=platform, height=45, width=230, disabled=True) for platform in platforms]

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
        
    
    def stream_webcam(container: ft.Container):
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
          command = [ 
                'ffmpeg',
                '-y',
                '-f', 'rawvideo',
                '-vcodec', 'rawvideo',
                '-pixel_format', 'bgr24',
                '-s', '640x480',
                '-framerate', '30',
                '-i', video_pipe_name,
                '-f', 's16le',
                '-ac', '2',
                '-ar', '44100',
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
          process = subprocess.Popen(command, stdin=subprocess.PIPE)
          global curtain
          global current_curtain_selected

          global video_thread_started
         
          if not video_thread_started:
                  video_thread_started = True
                  camera_thread = threading.Thread(target=capture_camera_video, args=(video_pipe_name,))
                  camera_thread.start()
                  audio_thread = threading.Thread(target=microphone_audio_stream, args=(current_curtain_selected,))
                  audio_thread.start()
                  # mic_thread.start()
          # while True:
          #   frame = frame_queue.get()
          #   audio_frame = audio_queue.get()
          #   if frame is None:
          #       break
          #   process.stdin.write(frame)
          #   process.stdin.write(audio_frame)
            # if curtain:
            #     if not video_audio_thread.is_alive():
            #         mic_stop_event.set()
            #         mic_thread.join()
            #         video_audio_stop_event.clear()
            #         video_audio_thread.start()
            # else:
            #     if not mic_thread.is_alive():
            #         video_audio_stop_event.set()
            #         video_audio_thread.join()
            #         mic_stop_event.clear()
            #         mic_thread.start()

            # if not audio_queue.empty():
            #     audio_data = audio_queue.get()
            #     process.stdin.write(audio_data)
    def capture_screen(container: ft.Container):
        youtube_key = "yj7j-fuzw-7370-6956-38t8"
        rtmp_url="rtmp://a.rtmp.youtube.com/live2/{}" 
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
        p = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)

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
        if drop_list.value == '1':
          thread = threading.Thread(target=capture_screen, args=(content._get_children()[0]._get_children()[0],))
          thread.start()
        else:
          thread = threading.Thread(target=stream_webcam, args=(content._get_children()[0]._get_children()[0],))
          thread.start()
        if stream_started:
          stream_started = False
          stream_button.text = "Iniciar transmisión"
        else:
          stream_started = True
          stream_button.text = "Detener transmisión"
        stream_button.update()

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
      print("Cortina", curtain)
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
          print("Iniciando cortina", boton_cortina.text)
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
    def click(e):
      global current_curtain_selected
      global video_added
      if video_added:
        video.playlist_remove(0)
      current_curtain_selected = e.control.key
      
      video.playlist_add(ft.VideoMedia(e.control.key))
      video.next()
      page.update()
      video_added = True
      extract_audio_thread = threading.Thread(target=extract_audio, args=(current_curtain_selected, AUDIO_OUTPUT))
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
      print("Plataforma", PLATFORM)
      if PLATFORM == 'Windows':
        result = subprocess.run(['ffmpeg', '-f', 'dshow', '-list_devices', 
                                 'true', '-i', 
                                 'dummy'], capture_output=True, text=True,
                                encoding='utf-8')
      elif PLATFORM == 'Darwin': 
        print("MacOS") 
        result = subprocess.run(['ffmpeg', '-f', 'avfoundation', 
                                 '-list_devices', 'true', '-i', ''], 
                                capture_output=True, text=True)
      
      output = result.stderr
      video_devices = []
      audio_devices = []

      

      for line in output.split('\n'):
        match = re.search(r'"(.*?)" \((.*?)\)', line)
        if match:
            device_name = match.group(1)
            device_type = match.group(2)
            if device_type == "video" and device_name not in video_devices:
                video_devices.append(device_name)
            elif device_type == "audio" and device_name not in audio_devices:
                audio_devices.append(device_name)
    
      return video_devices, audio_devices
    
    def play_audio(audio_device):
      subprocess.run(['ffplay', '-nodisp', '-autoexit', '-f', 'dshow', '-i', f'audio={audio_device}'])  

    def capture_curtain_video():
        global curtain_video_cap
        global stream_started
        curtain_video_cap = cv2.VideoCapture(current_curtain_selected)
        while True:
            if curtain:
                ret, curtain_frame = curtain_video_cap.read()
                if not ret:
                    curtain_video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue

                curtain_frame = cv2.resize(curtain_frame, (640, 480))
                if PLATFORM == 'Windows':
                  win32file.WriteFile(video_pipe, curtain_frame.tobytes())
                else:
                  video_pipe.write(curtain_frame.tobytes())
                  video_pipe.flush()
            if not stream_started:
                break
        curtain_video_cap.release()
        if PLATFORM == 'Windows':
          win32file.CloseHandle(video_pipe)
        else:
          video_pipe.close()
          os.remove(video_pipe_name)
          
    def capture_camera_video(pipe_name):
        global stream_video_cap
        stream_video_cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        global stream_started
        # stream_video_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        # stream_video_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        pipe = create_pipe(pipe_name)
        if PLATFORM == 'Windows':
          win32pipe.ConnectNamedPipe(pipe, None)
        try: 
          while True:
              ret, camera_frame = stream_video_cap.read()
              if not ret:
                  print("No se pudo leer el fotograma de la cámara")
                  break
              
              camera_frame = cv2.resize(camera_frame, (640, 480))
              ret, camera_frame_bytes = cv2.imencode('.png', camera_frame)
              camera_frame_base64 = base64.b64encode(camera_frame_bytes).decode('utf-8')

              if curtain and camera_frame_base64:
                  if img_preview:
                      img_preview.src_base64 = camera_frame_base64
                  if preview_container:
                      preview_container.content = img_preview
                      #page.update()
                      streaming_and_preview_row.update()
                      preview_container.update()
              else:
                  if img_live and camera_frame_base64:
                      img_live.src_base64 = camera_frame_base64
                      live_container.content = img_live
                      #page.update()
                      img_live.update()
                  if live_container:
                      live_container.update()
                  if PLATFORM == 'Windows':
                    win32file.WriteFile(pipe, camera_frame.tobytes())
                  else:
                    pipe.write(camera_frame.tobytes())
                    pipe.flush()
              if not stream_started:
                  break
        except Exception as e:
            print(f"Error en capture_camera_video: {e}")
            
        finally:
            stream_video_cap.release()
            if PLATFORM == 'Windows':
              win32pipe.DisconnectNamedPipe(pipe)
              win32file.CloseHandle(pipe)
            else:
              pipe.close()
              os.remove(pipe_name)
            #frame_queue.put(camera_frame.tobytes())

                # time.sleep(1 / 30.0)  # Control de la velocidad de fotogramas

    def switch_to_video_audio():
        command = [
        'ffmpeg',
        '-i', current_curtain_selected,
        '-vn',  # No video
        '-f', 'lavfi',
        '-i', 'anullsrc=r=44100:cl=stereo',
        '-filter_complex',
        '[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[a]',  # Mix audio inputs
        '-map', '[a]',
        '-f', 'wav',
        'pipe:1'
    ]
        return subprocess.Popen(command, stdout=process.stdin)

    def switch_to_microphone(process):
        global audio_process
        if audio_process:
            audio_process.terminate()
        null_audio_command = [
            'ffmpeg',
            '-f', 'lavfi',
            '-i', 'anullsrc=r=44100:cl=stereo',
            '-f', 'wav',
            'pipe:1'
        ]
        audio_process = subprocess.Popen(null_audio_command, stdout=process.stdin)

    video_devices, audio_devices = show_devices()

    options = [ft.dropdown.Option('1', "Grabar pantalla")] + [ft.dropdown.Option(device, device) for i, device in enumerate(video_devices)]

    drop_list = ft.Dropdown(
          width=380,
          options=options,
          on_change=on_change_drop_list,
          label="Dispositivo de video"
        )    
    audio_options = [ft.dropdown.Option('1', "Grabar audio de la pantalla")] + [ft.dropdown.Option(device, device) for i, device in enumerate(audio_devices)]
    audio_drop_list = ft.Dropdown(
          width=380,
          options=audio_options,
          on_change=on_change_drop_list,
          label="Dispositivo de audio"
        )
    pick_files_dialog = ft.FilePicker(on_result=show_file)
    selected_files = ft.Text()
    page.set_overlay(pick_files_dialog)
    #page.page.overlay.append(pick_files_dialog)
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
            on_exit_fullscreen=lambda e: print("Video salió de pantalla completa!"),
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
            on_exit_fullscreen=lambda e: print("Video salió de pantalla completa!"),
    ) 
    
    preview_container = ft.Container(
        bgcolor=ft.colors.BLACK,
        width=700,
        height=350,
        content=video
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
                                      allowed_extensions=["mp4", "avi"]
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
                          title=ft.Text("Configuración de transmisión"),
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


## MAKE 7 - 7 SECUENCE UNTIL 177
