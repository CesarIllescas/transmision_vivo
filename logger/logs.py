import logging

# Create a custom formatter with your desired time format
time_format = "%Y-%m-%d %H:%M:%S"
formatter = logging.Formatter(fmt='%(asctime)s  %(levelname)s\t%(message)s', datefmt=time_format)
# Create a logger and set the custom formatter
logging.basicConfig(level='INFO')
logger = logging.getLogger('__name__')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.propagate = False 
log = logger