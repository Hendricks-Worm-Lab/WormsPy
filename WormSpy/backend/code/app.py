from flask import Flask, request, Response, jsonify, render_template, abort
from flask_cors import CORS, cross_origin
import EasyPySpin
import cv2
from dlclive import DLCLive, Processor
import math
from zaber_motion import Library, Units
from zaber_motion.binary import Connection, Device, CommandCode
import numpy as np
import pytz
from datetime import datetime
import os
import copy
import threading
import imageio
# from flask_sockets import Sockets
# from flask_socketio import SocketIO, emit

app = Flask(__name__, template_folder='production\\templates',
            static_folder='production\\static')
CORS(app, origins=['http://localhost:4200', 'http://localhost:5000', 'https://4dfklk7l-4200.use.devtunnels.ms'])
app.config['CORS_HEADERS'] = 'Content-Type'
# sockets = Sockets(app)
# socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize the device database for Zaber communication
Library.enable_device_db_store()

# Start the Flask app locally on host IP address 127.0.0.1
# python -m flask run --host=127.0.0.1

# SET UP CONFIGURATION
TOTAL_MM_X = 1.3125  # total width of the FOV in mm
TOTAL_MM_Y = 1.05  # total height of the FOV in mm

# whether the x direction of the zaber is inverted from the video feed (-1 if they are inverted)
ZABER_ORIENTATION_X = 1
# whether the y direction of the zaber is inverted from the video feed (-1 if they are inverted)
ZABER_ORIENTATION_Y = -1

# UNIT CONVERSIONS
MM_MST = 20997  # millimeters per microstep

# Zaber device boundaries
MAXIMUM_DEVICE_XY_POSITION = 1066667
MAXIMUM_DEVICE_Z_POSITION = 209974
MINIMUM_DEVICE_POSITION = 0

# Global Variables for recording
stop_stream = False
start_recording = False
stop_recording = False
start_recording_fl = False
stop_recording_fl = False
timeZone = pytz.timezone("US/Eastern")
settings = {
    "resolution": (1920, 1200),
    "fps": 10,
    "filepath": 'D:\WormSpy_video\Tracking',
    "filename": 'default.avi',
    "resolution_fl": (960, 600),
    "fps_fl": 10,
    "filepath_fl": 'D:\WormSpy_video\Calcium',
    "filename_fl": 'default_fluorescent'
}

# DLC Live Settings
downsample_by = 4
TOTAL_PIXELS_X = 480  # pixels across of the video feed after downsampling
TOTAL_PIXELS_Y = 300  # pixels across of the video feed after downsampling

# Autofocus settings
af_enabled = False
start_af = False

# Intial Camera Settings
leftCam = None
rightCam = None
XYmotorport = 'COM4'
Zmotorport = 'COM3'

# Tracking Variables
is_tracking = False
start_tracking = False
nodeIndex = 0

# variables for histogram
hist_frame = None
 
# Fluorescent Camera Settings
fluorExposure = 40000
fluorGain = 0

@app.route("/")
@cross_origin()
def home():
    return render_template('index.html')

@cross_origin()
@app.route('/video_feed')
def video_feed():
    global leftCam
    # Open the video capture
    cap = EasyPySpin.VideoCapture(leftCam)
    # Check if the camera is opened successfully
    if not cap.isOpened():
        print("Camera can't open\nexit")
        return -1

    # Import the DLC NN model
    dlc_proc = Processor()
    dlc_live = DLCLive('../../../DLC_models/3-node',
                       processor=dlc_proc, display=False)

    # function to generate a stream of image frames for the tracking video feed
    def gen():
        global start_recording, stop_recording, settings, is_tracking, start_tracking, serialPort, af_enabled, start_af, stop_stream, nodeIndex
        start_af = False
        start_recording = False
        stop_recording = False
        is_recording = False
        #afRollingAvg = []
        #afMotorPos = []
        xPos = 0
        yPos = 0
        #zPos = 0
        xCmd = 0
        yCmd = 0
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        out = cv2.VideoWriter()
        with Connection.open_serial_port(XYmotorport) as connection:
            with Connection.open_serial_port(Zmotorport) as connection2:
                device_listXY = connection.detect_devices()
                # device_listZ = connection2.detect_devices() # Error occuring here
                xMotor = device_listXY[0]
                yMotor = device_listXY[1]
                # zMotor = device_listZ[0]
                # device_list[0].
                firstIt = True
                counter = 0
                while (cap.isOpened()):
                    # Read a frame from the video capture
                    success, frame = cap.read()
                    # Check if the frame was successfully read
                    if stop_stream:
                        abort(200)
                    elif success:
                        # Resize the image to be 256x256
                        img_dlc = cv2.resize(
                            frame, None, fx=1/downsample_by, fy=1/downsample_by)
                        if firstIt:
                            dlc_live.init_inference(img_dlc)
                            firstIt = False
                        poseArr = dlc_live.get_pose(img_dlc)
                        posArr = poseArr[:, [0, 1]]
                        nodePointX = poseArr[nodeIndex, 0]
                        nodePointY = poseArr[nodeIndex, 1]
                        # confArr = poseArr[:, [2]]
                        if start_tracking:
                            xPos = xMotor.get_position(unit=Units.NATIVE)
                            yPos = yMotor.get_position(unit=Units.NATIVE)
                            start_tracking = False
                        if is_tracking and counter % 1 == 0:
                            xPos, yPos, xCmd, yCmd = trackWorm(
                                (nodePointX, nodePointY), xMotor, yMotor, xPos, yPos)
                        counter += 1
                        # Reinitialize file recording
                        if start_recording:
                            print("Start Recording")
                            dt = datetime.now(tz=timeZone)
                            dtstr = '_' + dt.strftime("%d-%m-%Y_%H-%M-%S")
                            #save as gif cuz fuck opencv
                            output_filename = settings["filepath"] + settings["filename"] + dtstr + '.gif'
                            frames = []  # List to store frames for the GIF
                           # out.open(settings["filepath"] + settings["filename"] + dtstr + '.avi',
                           #          fourcc, settings["fps"], settings["resolution"], isColor=False)
                            start_recording = False
                            is_recording = True
                            csvDump = np.zeros((1, 2))
                        # add frame to recording buffer if currently recording
                        if is_recording:
                            # factor2 = cap.get(3) / (cap.get(3) * SCALE_FACTOR)
                            # posArr2 = [
                            # tuple(map(lambda x: int(abs(x) * factor2), i)) for i in posArr]
                            # posArr2 = np.append(posArr2, confArr, axis=1) # uncommentate to get confidence values in CSV, change shape to 0,3
                            # poseDump = np.append(poseDump, posArr2, axis=0)
                            csvDump = np.append(
                                csvDump, [[xCmd, yCmd]], axis=0)
                            im_out = cv2.resize(frame, [cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)])
                            #out.write(im_out)
                            # add frame to gif buffer
                            frames.append(im_out)
                        # convert recording buffer to file
                        if stop_recording:
                            print("Stopped Recording")
                            #out.release()
                            #save gif file
                            imageio.mimsave(output_filename, frames, 'GIF', fps=10)
                            dt = datetime.now()
                            dtstr = '_' + dt.strftime("%d-%m-%Y_%H-%M-%S")
                            np.savetxt(
                                settings["filepath"] + settings["filename"] + dtstr + ".csv", csvDump, delimiter=",")
                            is_recording = False
                            stop_recording = False
                        # get motor position on first iteration of focuslock
                        # if start_af:
                        #     zPos = zMotor.get_position(unit=Units.NATIVE)
                        #     afMotorPos.append(zPos)
                        #     start_af = False
                        # # move motor to the position of the better focus value.
                        # if af_enabled:
                        #     focus = determineFocus(
                        #         cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                        #     # mPos = zMotor.get_position(unit = Units.NATIVE)
                        #     afRollingAvg.append(focus)
                        #     if len(afRollingAvg) > 5:
                        #         afRollingAvg.pop(0)
                        #         # 100 Microsteps seems to be a good sweet spot
                        #         afMotorPos.pop(0)
                        #     zPos = setFocus(
                        #         zMotor, focus, afRollingAvg, afMotorPos)
                        #     afMotorPos.append(zPos)
                        factor = cap.get(3) / (cap.get(3) * (1/downsample_by))
                        posArr = [
                            tuple(map(lambda x: int(abs(x) * factor), i)) for i in posArr]
                        # Change color to rgb from bgr to allow for the coloring of circles
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
                        # add skeleton overlay to image
                        frame = draw_skeleton(frame, posArr)

                        ret, jpeg = cv2.imencode('.png', frame)
                        # Yield the encoded frame
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
                    else:
                        # If the frame was not successfully read, yield a "blank frame
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n\r\n')
    # Return the video feed as a multipart/x-mixed-replace response
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')


@cross_origin()
@app.route('/video_feed_fluorescent')
def video_feed_fluorescent():
    global rightCam, stop_stream
    cap2 = EasyPySpin.VideoCapture(rightCam)
    # Check if the camera is opened successfully
    if not cap2.isOpened():
        print("Camera can't open\nexit")
        return -1
    if stop_stream:
        abort(200)
    def gen():
        global start_recording_fl, stop_recording_fl, settings, is_tracking, serialPort, fluorExposure, fluorGain, fluorFPS, hist_frame
        is_recording = False
        cap2.set(cv2.CAP_PROP_EXPOSURE, fluorExposure)
        cap2.set(cv2.CAP_PROP_GAIN, fluorGain)
        while (cap2.isOpened()):
            # Read a frame from the video capture
            success, frame = cap2.read()
            # Check if the frame was successfully read
            if success:
                # Apply the jet color map to the frame
                frame_8bit = cv2.normalize(frame, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
                #frame_8bit_c = cv2.applyColorMap(frame_8bit, cv2.COLORMAP_JET)
                # hist_frame = frame
                hist_frame = copy.copy(frame_8bit)
                if start_recording_fl:
                    print("Start Fluorescent Recording")
                    # out.open(settings["filepath_fl"] + settings["filename_fl"], fourcc, float(settings["fps_fl"]), settings["resolution_fl"], isColor=False)
                    start_recording_fl = False
                    is_recording = True
                    frame_count = 0
                    dt = datetime.now(tz=timeZone)
                    dtstr = dt.strftime("%d-%m-%Y_%H-%M-%S")
                    folder_name = settings["filename_fl"] + '_' + dtstr
                    path = os.path.join(settings["filepath_fl"], folder_name)
                    os.mkdir(path)
                if is_recording:
                    frame_count += 1
                    frame = cv2.resize(frame, [cap2.get(cv2.CAP_PROP_FRAME_WIDTH), cap2.get(cv2.CAP_PROP_FRAME_HEIGHT)])
                    cv2.imwrite(
                        f"{settings['filepath_fl']}\\{folder_name}\\frame_{frame_count}.tiff", frame)
                if stop_recording_fl:
                    print("Stopped Fluorescent Recording")
                    # out.release()
                    is_recording = False
                    stop_recording_fl = False

                ret, jpeg = cv2.imencode('.png', frame_8bit)
                # Yield the encoded frame
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            else:
                # If the frame was not successfully read, yield a blank frame
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n\r\n')
    # Return the video feed as a multipart/x-mixed-replace response
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

lock = threading.Lock()

@cross_origin()
@app.route("/get_hist")
def get_hist():
    def gen():
        global hist_frame
        current_frame = np.zeros((1200, 1920, 1), dtype=np.uint8)
        first = True
        while True: 
            # if first:
            #     first = False
            #     print(hist_frame)
            # if hist_frame is not None:
            #     current_frame = hist_frame
            current_frame = hist_frame
                
            if current_frame is not None:
                # print(hist_frame.shape[0])
                # buffer = io.BytesIO()
                #hist_frame_8bit = cv2.normalize(current_frame, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
                hist_size = 256
                hist_w = 512
                hist_h = 400
                bin_w = int(round(hist_w / hist_size))
                hist = cv2.calcHist(current_frame, [0], None, [hist_size], (0, 256), accumulate=False)
                
                norm_hist = cv2.normalize(hist, hist, 0, hist_h, cv2.NORM_MINMAX)
                histImage = np.zeros((hist_h, hist_w, 3), dtype=np.uint8)
                # # Create an empty image
                # h = np.ones((256, 256, 3)) * 255  # Here, 300 is the height and 256 is the width
                for i in range(1, hist_size):
                    cv2.line(histImage, (bin_w * (i - 1), hist_h - int(norm_hist[i - 1])),
                             (bin_w * (i), hist_h - int(norm_hist[i])), (0, 255, 0), thickness=2)
                
                ret, image_data = cv2.imencode('.png', histImage)
                png = image_data.tobytes()
                # image_data = buffer.getvalue()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + png + b'\r\n')
            else:
                # If the frame was not successfully read, yield a blank frame
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n\r\n')
    # Return the video feed as a multipart/x-mixed-replace response
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')


# @cross_origin()
# @socketio.on('get_hist')
# def get_hist():
#     def gen():
#         global hist_frame
#         while True: 
#             if hist_frame is not None:
#                 buffer = io.BytesIO()
#                 hist = cv2.calcHist([hist_frame], [0], None, [256], [1, 256])
#                 hist = cv2.normalize(hist, hist, 1, 255, cv2.NORM_MINMAX)
#                 # plt.hist(hist, 256, [1, 256])
#                 plt.clf()
#                 plt.plot(hist)
#                 plt.savefig(buffer, format="png")
#                 image_data = buffer.getvalue()
#                 emit('get_hist', image_data)
#             elif hist_frame is None:
#                 # If the frame was not successfully read, yield a blank frame
#                 emit('get_hist', None)
#     # Start the generator as a background task
#     socketio.start_background_task(gen)


@cross_origin()
@app.route("/start_recording", methods=['POST'])
def start_recording():
    global start_recording, start_recording_fl, settings
    # Update the settings with the data from the request body
    settings["filepath"] = request.json["filepath"]
    settings["filename"] = request.json["filename"]
    settings["fps"] = request.json["fps"]
    settings["resolution"] = (
        request.json["resolution"], request.json["resolution"])
    settings["filepath_fl"] = request.json["filepath_fl"]
    settings["filename_fl"] = request.json["filename_fl"]
    settings["fps_fl"] = request.json["fps_fl"]
    settings["resolution_fl"] = (
        request.json["resolution_fl"], request.json["resolution_fl"])
    # Set the recording flag to True
    start_recording = True
    start_recording_fl = True
    # return jsonify({"message": "Recording started"})
    return str(settings)

@cross_origin()
@app.route("/stop_recording", methods=['POST'])
def stop_recording():
    global stop_recording, stop_recording_fl
    # Stop the recording of both video feeds
    stop_recording = True
    stop_recording_fl = True
    return jsonify({"message": "Recording stopped"})

@cross_origin()
@app.route("/stop_live_stream", methods=['POST'])
def stop_live_stream():
    global stop_stream
    # Stop both video feeds
    stop_stream = True
    return jsonify({"message": "Streams stopped"})

@cross_origin()
@app.route("/camera_settings", methods=['POST'])
def camera_settings():
    global leftCam, rightCam, serialPort
    # Set the camera settings before starting the video feeds
    leftCam = request.json['leftCam']
    rightCam = request.json['rightCam']
    serialPort = request.json['serialInput']
    return jsonify({"message": "Recording stopped"})

@cross_origin()
@app.route("/flour_settings", methods=['POST'])
def flour_settings():
    global fluorExposure, fluorGain, fluorFPS
    # Set the camera settings before starting the video feeds
    fluorExposure = request.json['exposure']
    fluorGain = request.json['gain']
    fluorFPS = request.json['fps']
    return jsonify({"message": "Fluorescent Settings Updated"})

@cross_origin()
@app.route("/node_index", methods=['POST'])
def node_index():
    global nodeIndex
    # Set the camera settings before starting the video feeds
    nodeIndex = request.json['index']
    return jsonify({f"message": "NodeIndex Recieved: {nodeIndex}"})

@cross_origin()
@app.route("/toggle_tracking", methods=['POST'])
def toggle_tracking():
    global is_tracking, start_tracking
    is_tracking = request.json['is_tracking'] == "True"
    # start tracking if tracking has been requested
    start_tracking = is_tracking
    return str(is_tracking)


@cross_origin()
@app.route("/toggle_af", methods=['POST'])
def toggle_af():
    global af_enabled, start_af
    af_enabled = request.json['af_enabled'] == "True"
    # start focuslock if tracking has been requested
    start_af = af_enabled
    return str(af_enabled)


def determineFocus(image):
    # determine focus using thresholding
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    abs_sobelx = cv2.convertScaleAbs(sobelx)
    focus_measure = int(cv2.Laplacian(abs_sobelx, cv2.CV_64F).var())
    return focus_measure


def setFocus(zMotor: Device, focus: int, afRollingAvg, afMotorPos):
    step = 30  # The amount the z motor moves with each call of the function
    mPos = afMotorPos[-1]
    if len(afRollingAvg) > 1 and len(afMotorPos) > 1:
        mPosDiff = afMotorPos[-1] - afMotorPos[-2]
        # current focus is worse than previous focus
        if focus < np.mean(afRollingAvg):
            # move towards previous position
            if mPosDiff <= 0 and mPos + step < MAXIMUM_DEVICE_Z_POSITION:
                zMotor.generic_command_no_response(
                    command=CommandCode.MOVE_RELATIVE, data=step)
                return (mPos + step)
            elif mPosDiff > 0 and mPos - step > MINIMUM_DEVICE_POSITION:
                zMotor.generic_command_no_response(
                    command=CommandCode.MOVE_RELATIVE, data=-step)
                return (mPos - step)
        # current focus is better than previous focus
        elif focus > np.mean(afRollingAvg):
            if mPosDiff <= 0 and mPos - step > MINIMUM_DEVICE_POSITION:
                zMotor.generic_command_no_response(
                    command=CommandCode.MOVE_RELATIVE, data=-step)
                return (mPos - step)
            elif mPosDiff > 0 and mPos + step < MAXIMUM_DEVICE_Z_POSITION:
                zMotor.generic_command_no_response(
                    command=CommandCode.MOVE_RELATIVE, data=step)
                return (mPos + step)
    return mPos


def simpleToCenter(centroidX, centroidY):
    # calculate the percent the position is from the edge of the frame
    percentX = float(centroidX) / float(TOTAL_PIXELS_X)
    percentY = float(centroidY) / float(TOTAL_PIXELS_Y)

    # millimeters the position is from the edge
    millisX = percentX * TOTAL_MM_X
    millisY = percentY * TOTAL_MM_Y

    # millimeters the stage needs to move to catch up to the worm's position
    millisMoveX = ZABER_ORIENTATION_X * (millisX - TOTAL_MM_X/2)
    millisMoveY = ZABER_ORIENTATION_Y * (millisY - TOTAL_MM_Y/2)

    return millisMoveX, millisMoveY


def trackWorm(input, deviceX: Device, deviceY: Device, deviceXPos, deviceYPos):
    # check if the input is NaN float value and return if so
    if math.isnan(input[0]):
        return 0, 0

    # relative worm position is relative to the (0, 0) of the video feed
    master = simpleToCenter(input[0], input[1])

    # convert the millimeters back to microsteps
    xCmdAmt = master[0] * MM_MST
    yCmdAmt = master[1] * MM_MST

    # chill factor is used to slow down the movement of the stage
    chill_factor = 15
    # move device if the bounds of the device are not exceeded
    if (deviceXPos + xCmdAmt/10 < MAXIMUM_DEVICE_XY_POSITION
        or deviceXPos + xCmdAmt/10 > MINIMUM_DEVICE_POSITION
        or deviceYPos + yCmdAmt/10 < MAXIMUM_DEVICE_XY_POSITION
            or deviceYPos + yCmdAmt/10 > MINIMUM_DEVICE_POSITION):
        deviceX.generic_command_no_response(
            command=CommandCode.MOVE_RELATIVE, data=int(xCmdAmt/chill_factor))
        deviceY.generic_command_no_response(
            command=CommandCode.MOVE_RELATIVE, data=int(yCmdAmt/chill_factor))
    return (deviceXPos + xCmdAmt/10), (deviceYPos + yCmdAmt/10), xCmdAmt, yCmdAmt


def draw_skeleton(frame, posArr):
    # Line and circle attributes
    linecolor = (0, 0, 0)
    lineThickness = 2
    circleThickness = -1
    circleRadius = 5

    # Colors of the different worm parts
    noseTipColor = (0, 0, 255)
    pharynxColor = (0, 128, 255)
    nerveRingColor = (0, 255, 255)
    # midbody1Color = (0, 255, 0)
    # midbody2Color = (255, 0, 0)
    # midbody3Color = (255, 0, 255)
    # tailBaseColor = (0, 0, 255)
    # tailTipColor = (255, 0, 0)
    # confArr = poseArr[:, 2]
    # Overlay the tracking data onto the image
    # line from nose tip to pharynx
    cv2.line(frame, posArr[0], posArr[1], linecolor, lineThickness)
    # line from pharynx to nerve_ring
    cv2.line(frame, posArr[1], posArr[2], linecolor, lineThickness)
    # line from nerve ring to midbody1
    # cv2.line(frame, posArr[2], posArr[3], linecolor, lineThickness)
    # # line from midbody1 to midbody2
    # cv2.line(frame, posArr[3], posArr[4], linecolor, lineThickness)
    # # line from midbody2 to midbody3
    # cv2.line(frame, posArr[4], posArr[5], linecolor, lineThickness)
    # # line from midbody3 to tail_base
    # cv2.line(frame, posArr[5], posArr[6], linecolor, lineThickness)
    # # line from tail_base to tail_tip
    # cv2.line(frame, posArr[6], posArr[7], linecolor, lineThickness)

    # draw circles on top of each worm part
    cv2.circle(frame, posArr[0], circleRadius, noseTipColor, circleThickness)
    cv2.circle(frame, posArr[1], circleRadius, pharynxColor, circleThickness)
    cv2.circle(frame, posArr[2], circleRadius, nerveRingColor, circleThickness)
    # cv2.circle(frame, posArr[3], circleRadius, midbody1Color, circleThickness)
    # cv2.circle(frame, posArr[4], circleRadius, midbody2Color, circleThickness)
    # cv2.circle(frame, posArr[5], circleRadius, midbody3Color, circleThickness)
    # cv2.circle(frame, posArr[6], circleRadius, tailBaseColor, circleThickness)
    # cv2.circle(frame, posArr[7], circleRadius, tailTipColor, circleThickness)

    return frame

if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
    # socketio.run(app, host='127.0.0.1', port=5000, debug=False)