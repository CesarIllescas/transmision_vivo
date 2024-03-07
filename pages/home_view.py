from typing import Union
import flet as ft
from pages.Router import Router, DataStrategyEnum
from State import State
import cv2 
import numpy as np
import pyautogui
import threading
import base64
import subprocess

def HomeView(page: ft.Page):
    
    #Componentes con su value favor de no mover
    def on_change_drop_list(e):
      page.update()
      
    def on_change_server_input(e):
      page.update()
    
    drop_list = ft.Dropdown(
      width=380,
      options=[
        ft.dropdown.Option('1',"Grabar pantalla"),
        ft.dropdown.Option('2',"Grabar desde cámara"),
      ],
      on_change=on_change_drop_list
    )
    img = ft.Image(
          src="statics/images/black.png",
          border_radius=ft.border_radius.all(20)
      )

    server_input = ft.TextField(
      width=380,
      label="Servidor",
      on_change=on_change_server_input
    )
    
    transmition_key = ft.TextField(
      width=380,
      label="Clave de transmisión",
      on_change=on_change_server_input
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
    def capture_screen(container: ft.Container):
      if drop_list.value == '1':
          print(drop_list.value)

          capture = None
      else:
          capture = cv2.VideoCapture(0)
          capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
          capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

      rtmp_url = f'{server_input.value}/{transmition_key.value}'
      # rtmp_url = "rtmp://212.227.239.4:1935/live/blytSOoF1"
      # fps = int(capture.get(cv2.CAP_PROP_FPS))
      # width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
      # height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

      command = ['ffmpeg',
                '-y',
                '-f', 'rawvideo',
                '-vcodec', 'rawvideo',
                '-pix_fmt', 'bgr24',
                '-s', "1920x1080",
               '-use_wallclock_as_timestamps', '1',
                 '-i', 
                  '-',
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',
                '-preset', 'ultrafast',
                '-f', 'flv',
                rtmp_url]

      p = subprocess.Popen(command, stdin=subprocess.PIPE)

      while True:
          if drop_list.value == '1':
              # print("Capturando pantalla")
              screen = pyautogui.screenshot()
              frame = np.array(screen)
              frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
          else:
              ret, frame = capture.read()
          try:
            p.stdin.write(frame.tobytes())
          except:
            break
          ret, frame_bytes = cv2.imencode('.png', frame)
          frame_base64 = base64.b64encode(frame_bytes).decode('utf-8')

          img.src_base64 = frame_base64
          page.update()
    def on_Iniciar_transmision_click(e):
        thread = threading.Thread(target=capture_screen, args=(content._get_children()[0]._get_children()[0],))
        thread.start()
    content = ft.Column(
            [
                ft.Row(
                [
                    ft.Container(
                      bgcolor=ft.colors.BLACK,
                      width=800,
                      height=400,
                      content=img
                    )
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
                          drop_list,
                          server_input,
                          transmition_key,
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
                          title=ft.Text("Audio"),
                          bgcolor=ft.colors.BLACK38,
                        ),
                        ft.Column([
                          ft.Container(
                            content=ft.CupertinoSlider(
                                      divisions=100,
                                      max=100,
                                      width=400,
                                      active_color=ft.colors.BLUE,
                                      thumb_color=ft.colors.BLUE,
                                      on_change_start=handle_change_start,
                                      on_change_end=handle_change_end,
                                      on_change=handle_change,
                                    )
                          )
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
                          title=ft.Text("Controles"),
                          bgcolor=ft.colors.BLACK38,
                        ),
                        ft.Column([
                          ft.TextButton(
                            "Iniciar transmisión",
                            width=400,
                            on_click=on_Iniciar_transmision_click
                          ),
                          ft.TextButton(
                            "Iniciar grabación",
                            width=400
                          ),
                          ft.TextButton(
                            "Configuración de transmisión",
                            width=400
                          ),
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

