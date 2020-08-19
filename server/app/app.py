# deps for routing and upload
import os
import asyncio
from flask import Flask, request, redirect, session, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from flask_cors import CORS
import argparse
import glob
import json
import mimetypes
import random
import os
import sys

#  deps for processing media
import imageio
import imageio.plugins.ffmpeg
import tqdm
from typing import Dict
import colorama
from colorama import Fore, Style

from .utils.centerface import CenterFace
from .utils.handle_frames import draw_replacements, process_frame, image_detect

# TODO validation
# from .allowedFile import allowedFileExtension, allowedFileType
from pathlib import Path
from dotenv import load_dotenv

app = Flask(__name__)
UPLOAD_FOLDER = "static"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
with open("./config.json") as f:
    config = json.load(f)
app.config.update(config)
# app.debug = True
# used to set env to development since that is preferred over setting it in config file
env_path = Path(".") / ".env"
load_dotenv(dotenv_path=env_path)
# set the client's upload folder to the static dir, making contents accessible
# on the client side
app.config["DEBUG"] = True
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
# Expose cors headers to enable file download
CORS(app, expose_headers="Authorization")


@app.route("/upload", methods=["GET", "POST"])
def fileUpload():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file part")
            return redirect(request.url)
        file = request.files["file"]
        if file.filename == "":
            flash("No selected file")

    f_path = os.path.join(app.root_path, app.config["UPLOAD_FOLDER"])
    if not os.path.isdir(f_path):
        os.mkdir(f_path)

    file = request.files["file"]
    file_options = request.form.get("fileOptions")
    filename = secure_filename(file.filename)
    mime_type = file.content_type

    # validFileType = allowedFileType(mime_type)
    # if not allowedFileExtension(file.filename) or validFileType == False:
    destination = "/".join([f_path, filename])
    # need this? save in memory or session instead?
    file.save(destination)
    file_length = os.stat(destination).st_size
    session["uploadFilePath"] = destination
    mime = mimetypes.guess_type(destination)[0]
    if mime is None:
        return None

    processed_file_title = filename.split(".")[0]
    processed_file_ext = filename.split(".")[1]
    processed_file_name = f"{processed_file_title}_{file_options}.{processed_file_ext}"

    session["processedFileName"] = processed_file_name

    emoji = {
        "base_path": f"{f_path}/emojis/",
        "path": "",
        "type": "",
        "selected": "",
        "resolved": False,
    }

    if mime.startswith("video"):
        if file_options == "emoji":
            emoji["type"] = "video"
        face_replace(
            destination, file_options, "video", emoji,
        )
        return send_from_directory(f_path, processed_file_name, as_attachment=True)

    elif mime.startswith("image"):
        if file_options == "emoji":
            emoji["type"] = "image"
        face_replace(
            destination, file_options, "image", emoji,
        )
        return send_from_directory(f_path, processed_file_name, as_attachment=True)

    else:
        print("unknown mimetype")
        return ""


@app.route("/download/<path:filename>", methods=["GET", "POST"])
def download(filename):
    uploads = os.path.join(current_app.root_path, app.config["UPLOAD_FOLDER"])
    return send_from_directory(directory=uploads, filename=filename)


@app.route("/", defaults={"u_path": ""})
@app.route("/<path:u_path>")
def catch_all(u_path):
    print("in catch_all, args are: ", sys.argv)
    print(repr(u_path))
    return "ok"


# moved this into app from face_replace to attempt to track frame processing progress, and pass to front end
def video_detect(
    ipath: str,
    opath: str,
    centerface: str,
    threshold: float,
    nested: bool,
    replacewith: str,
    emoji: str,
    mask_scale: float,
    ellipse: bool,
    ffmpeg_config: Dict[str, str],
):
    try:
        reader: imageio.plugins.ffmpeg.FfmpegFormat.Reader = imageio.get_reader(ipath)
        meta = reader.get_meta_data()
        _ = meta["size"]
    except:
        print(
            Fore.RED
            + f"Could not open file {ipath} as a video file with imageio. Make sure ffmpeg is installed on system, and try converting video to MP4 format"
        )
        return

    read_iter = reader.iter_data()
    print("read iter is ", read_iter)
    nframes = reader.count_frames()
    print("n frames is ", nframes)
    print("initializing frame sessions")
    session["frames"] = nframes
    print("session frames ", session["frames"])
    print(Fore.GREEN + "Step 3: Process Frames and Draw Replacements")
    if nested:
        bar = tqdm.tqdm(dynamic_ncols=True, total=nframes, position=1, leave=True)
    else:
        bar = tqdm.tqdm(dynamic_ncols=True, total=nframes)

    if opath is not None:
        writer: imageio.plugins.ffmpeg.FfmpegFormat.Writer = imageio.get_writer(
            opath, format="FFMPEG", mode="I", fps=meta["fps"], **ffmpeg_config
        )
    for frame in enumerate(read_iter):
        frame_index, current_frame = frame
        # Store session value here for rendering progress on frontend
        session["frame"] = frame_index
        # Perform network inference, get bb dets but discard landmark predictions
        dets, _ = centerface(current_frame, threshold=threshold)
        # TODO: refactor with loop over dets here, select emoji
        process_frame(
            dets,
            current_frame,
            mask_scale=mask_scale,
            replacewith=replacewith,
            emoji=emoji,
            ellipse=ellipse,
        )

        if opath is not None:
            writer.append_data(current_frame)
        bar.update()
    reader.close()
    if opath is not None:
        writer.close()
    bar.close()


def face_replace(file, file_options, filetype, emoji):
    print(Fore.GREEN + "Step 1 Processing file... ", file)
    ipaths = [file]
    base_opath = None
    replacewith = file_options
    emoji = emoji
    threshold = 0.2
    ellipse = True
    mask_scale = 1.3
    # TODO: debug auto codec
    # ffmpeg_config = {"codec": "libx264"}
    ffmpeg_config = {}
    # TODO pass backend from .env file or front end
    # backend = "auto"
    backend = "auto"
    in_shape = None
    if in_shape is not None:
        w, h = in_shape.split("x")
        in_shape = int(w), int(h)
        # TODO: this is never used

    # TODO: scalar downscaling setting (-> in_shape), preserving aspect ratio
    centerface = CenterFace(in_shape=in_shape, backend=backend)

    multi_file = len(ipaths) > 1
    if multi_file:
        ipaths = tqdm.tqdm(
            ipaths, position=0, dynamic_ncols=True, desc="Batch progress"
        )

    for ipath in ipaths:
        opath = base_opath
        if opath is None:
            root, ext = os.path.splitext(ipath)
            opath = f"{root}_{file_options}{ext}"
        print(Fore.BLUE + f"Input:  {ipath}\nOutput: {opath}")
        if opath is None:
            print(Fore.RED + "No output file is specified, no output will be produced.")
        if filetype == "video":
            print(Fore.GREEN + "Step 2: Video Detect")
            video_detect(
                ipath=ipath,
                opath=opath,
                centerface=centerface,
                threshold=threshold,
                replacewith=replacewith,
                emoji=emoji,
                mask_scale=mask_scale,
                ellipse=ellipse,
                nested=multi_file,
                ffmpeg_config=ffmpeg_config,
            )
        elif filetype == "image":
            print(Fore.GREEN + "Step 2: Image Detect")
            print(Fore.GREEN + "Step 3: Process Frames and Draw Replacements")
            image_detect(
                ipath=ipath,
                opath=opath,
                centerface=centerface,
                threshold=threshold,
                replacewith=replacewith,
                emoji=emoji,
                mask_scale=mask_scale,
                ellipse=ellipse,
            )
        else:
            print(
                Fore.RED + f"File {ipath} has an unknown type {filetype}. Skipping..."
            )


if __name__ == "__main__":
    main()
