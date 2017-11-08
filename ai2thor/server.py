import datetime
import io
import json
import logging
import os
import os.path
from queue import Empty
import random
import time
import uuid

from flask import Flask, request, make_response, abort
import werkzeug.serving
from PIL import Image
import numpy as np

LOG = logging.getLogger('werkzeug')
LOG.setLevel(logging.ERROR)


def format_sse(**params):
    lines = ["%s: %s" % (k, v) for k, v in params.items()]
    return "\n".join(lines) + "\n\n"

# get with timeout to allow quit
def queue_get(q):
    while True:
        try:
            r = q.get(block=False, timeout=0.5)
            return r
        except Empty:
            pass


class Event(object):

    def __init__(self, metadata, frame_id, frame):
        self.metadata = metadata
        self.frame_id = frame_id
        self.frame = frame

    # return frame as PNG
    def image(self):
        img = Image.fromarray(self.frame, mode='RGB')

        buf = io.BytesIO()
        img.save(buf, 'PNG')
        buf.seek(0)
        return buf.read()

class DiscreteModel(object):

    def __init__(self):
        self.events = []

    def next_action(self, event):
        methods = [
            'MoveAhead',
            'MoveBack',
            'LookUp',
            'LookDown',
            'RotateLeft',
            'RotateRight',
            'MoveRight',
            'MoveLeft']
        action = methods[random.randint(0, len(methods) - 1)]
        return dict(action=action)

class Model(object):

    def __init__(self):
        self.events = []

    def next_action(self, event):
        return random.randint(0, 5)

class Server(object):

    def __init__(self, request_queue, response_queue, port=0):

        app = Flask(__name__,
                    template_folder=os.path.realpath(
                        os.path.join(
                            os.path.dirname(os.path.abspath(__file__)), '..', 'templates')))

        self.image_buffer = None 

        self.app = app
        self.client_token = str(uuid.uuid4())
        self.subscriptions = []
        self.app.config.update(PROPAGATE_EXCEPTIONS=True, JSONIFY_PRETTYPRINT_REGULAR=False)
        self.port = port
        self.last_rate_timestamp = time.time()
        self.frame_counter = 0
        self.debug_frames_per_interval = 50
        self.xwindow_id = None
        self.wsgi_server = werkzeug.serving.BaseWSGIServer('127.0.0.1', self.port, self.app)
        # used to ensure that we are receiving frames for the action we sent
        self.sequence_id = 0

        @app.route('/ping', methods=['get'])
        def ping():
            return 'pong'

        @app.route('/train', methods=['post'])
        def train():

            if self.client_token:
                token = request.form['token']
                if token is None or token != self.client_token:
                    abort(403)

            if self.frame_counter % self.debug_frames_per_interval == 0:
                now = time.time()
                rate = self.debug_frames_per_interval / float(now - self.last_rate_timestamp)
                print("%s %s/s" % (datetime.datetime.now().isoformat(), rate))
                self.last_rate_timestamp = now

            metadata = json.loads(request.form['metadata'])
            if metadata['sequenceId'] != self.sequence_id:
                raise Exception("Sequence id mismatch: %s vs %s" % (
                    metadata['sequenceId'], self.sequence_id))

            #image = request.files['image']
            #image_data = image.read()
            filename = None #image.filename
            # decode image from string encoding
            image = None #np.asarray(Image.open(io.BytesIO(image_data))) 

            event = Event(metadata, filename, image)
            request_queue.put_nowait(event)
            self.frame_counter += 1

            next_action = queue_get(response_queue)
            if 'sequenceId' not in next_action:
                self.sequence_id += 1
                next_action['sequenceId'] = self.sequence_id
            else:
                self.sequence_id = next_action['sequenceId']

            resp = make_response(json.dumps(next_action))

            return resp

    def start(self):
        self.wsgi_server.serve_forever()