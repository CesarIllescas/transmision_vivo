import numpy as np
from numba import jit

@jit(nopython=True)
def resize_numba(image, new_width, new_height):
    height, width, channels = image.shape
    resized_image = np.zeros((new_height, new_width, channels), dtype=np.uint8)

    x_ratio = width / new_width
    y_ratio = height / new_height

    for i in range(new_height):
        for j in range(new_width):
            # Coordenadas originales
            x = int(x_ratio * j)
            y = int(y_ratio * i)

            x_diff = (x_ratio * j) - x
            y_diff = (y_ratio * i) - y

            for c in range(channels):
                # Pixels alrededor del punto (i, j)
                A = image[y, x, c]
                B = image[y, min(x + 1, width - 1), c]
                C = image[min(y + 1, height - 1), x, c]
                D = image[min(y + 1, height - 1), min(x + 1, width - 1), c]

                # Interpolación bilineal
                pixel = (A * (1 - x_diff) * (1 - y_diff) +
                         B * x_diff * (1 - y_diff) +
                         C * y_diff * (1 - x_diff) +
                         D * x_diff * y_diff)

                resized_image[i, j, c] = int(pixel)

    return resized_image
