import os
import subprocess
import time
from flask import Flask, jsonify, Response
from flask_cors import CORS
import json
import spacy
import firebase_admin
from firebase_admin import storage
import imageio_ffmpeg as ffmpeg

#deleting videos which are downloaded previously before start
files = os.listdir(os.getcwd())
for file in files:
    file_name, ext = os.path.splitext(file)
    if ext == '.mp4':
        try:
            os.remove(file)
        except PermissionError:
            time.sleep(1)


# Firebase initialization
cred = firebase_admin.credentials.Certificate('aeroweb27-firebase-adminsdk-oudwq-40e248fbf8.json')
firebase_admin.initialize_app(cred, {
    'storageBucket': 'aeroweb27.appspot.com'
})

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Cache video directory and Spacy model
video_directory_cache = None
nlp = None
video_directory_file_name = "video_directory_avis"
video_storage_folder = "videos_avis"
video_cache_list = []
previous_prompt = None

def load_nlp_model():
    global nlp
    if nlp is None:
        try:
            nlp = spacy.load("en_core_web_sm")
        except OSError:
            from spacy.cli import download
            download("en_core_web_sm")
            nlp = spacy.load("en_core_web_sm")

@app.route('/text/<string:prompt>')
def Tosignlang(prompt):
    global previous_prompt
    global video_directory_cache
    global video_directory_file_name

    print('\n\n\n\n\nPrompt: ' + prompt)

    if previous_prompt == prompt:
        if os.path.exists('stitched_output.mp4'):
            return Response(open('stitched_output.mp4', 'rb'), mimetype = 'video/mp4')
            #return stream_video('stitched_output.mp4')
    delete_video('stitched_output')
    previous_prompt = prompt

    # Download video_directory.json only if it's not already cached
    if video_directory_cache is None:
        bucket = storage.bucket()
        blob = bucket.blob(f'{video_directory_file_name}.json')
        blob.download_to_filename(f'{video_directory_file_name}.json')
        with open(f'{video_directory_file_name}.json', 'r') as json_file:
            video_directory_cache = json.load(json_file)
        os.remove(f'{video_directory_file_name}.json')
    
    # Load Spacy NLP model
    load_nlp_model()

    # Process the input prompt using NLP
    tfsentence = transform_sentence(prompt)
    notfound = stitch_videos_for_sentence(tfsentence, video_directory_cache)

    if os.path.exists('stitched_output.mp4'):
        return Response(generate_video_stream('stitched_output.mp4'), 
                        mimetype = 'video/mp4',
                        headers={
                            'Content-Type': 'video/mp4',
                            'Transfer-Encoding': 'chunked',
                            'Cache-Control': 'no-cache',
                            'Accept-Ranges': 'bytes'
                        });
        #response = stream_video('stitched_output.mp4')
        #return response
    else:
        return 'Failed to create stitched video', 404

# For developer purposes
@app.route('/files')
def listfiles():
    files = os.listdir(os.getcwd())
    return jsonify(files)

@app.route('/reconfigVideoDirectory')
def reconfig_video_directory():
    global video_directory_cache
    global video_directory_file_name

    bucket = storage.bucket()
    blob = bucket.blob(f'{video_directory_file_name}.json')
    blob.download_to_filename(f'{video_directory_file_name}.json')
    with open(f'{video_directory_file_name}.json', 'r') as json_file:
        video_directory_cache = json.load(json_file)
    os.remove(f'{video_directory_file_name}.json')

    return f"video directory reconfigured successfully\nDownloaded latest video directory: {video_directory_file_name}"

@app.route('/switchDatasetTo/<string:datasetname>')
def switch_dataset(datasetname):
    global video_directory_file_name
    global video_storage_folder

    if datasetname == 'test':
        video_directory_file_name = "video_directory"
        video_storage_folder = "videos"
    elif datasetname == 'avis':
        video_directory_file_name = "video_directory_avis"
        video_storage_folder = "videos_avis"
    
    reconfig_video_directory()
    delete_video_cache()
    return f"Dataset switched to video_directory_file_name = {video_directory_file_name}, and video_storage_folder = {video_storage_folder}"

@app.route('/deleteVideoCache')
def delete_video_cache():
    global video_cache_list
    global previous_prompt
    delete_video_parallel(video_cache_list)
    video_list_with_ext = [video_name+'.mp4' for video_name in video_cache_list]
    video_cache_list = []
    previous_prompt = None
    return json.dumps(video_list_with_ext)

@app.route('/deleteVideoFile/<string:file_name>')
def delete_video_file(file_name):
    global video_cache_list
    if os.path.exists(f'{file_name}.mp4'):
        delete_video(file_name)
        if f'pr_{file_name}.mp4' in video_cache_list:
            video_cache_list.remove(f'pr_{file_name}')
        return f'Video file {file_name}.mp4 deleted successfully.'
    else:
        return 'File not Found'

def transform_sentence(text):
    # Perform NLP sentence transformation
    doc = nlp(text)
    transformed_sentence = []

    for sent in doc.sents:
        words_in_sentence = [token.text for token in sent if token.pos_ not in ['AUX', 'DET', 'PART', 'SPACE', 'PUNCT', 'CCONJ', 'SCONJ']]
        cleaned_sentence = " ".join(words_in_sentence)

        for token in sent:
            if token.pos_ in ['AUX', 'DET', 'SPACE', 'PUNCT', 'CCONJ', 'SCONJ']:
                continue
            else:
                transformed_sentence.append(token.lemma_)

    return " ".join(transformed_sentence)

def stitch_videos_for_sentence(tfsentence, video_directory):
    words = tfsentence.lower().split()
    # video_clips = []
    notfound = []
    stitch_order = []

    # Download videos in parallel
    video_files_to_download = []

    for word in words:
        if word in video_directory:
            if word not in video_files_to_download:
                word = 'me' if word == 'i' else word
                video_files_to_download.append(word)
            stitch_order.append(word)
        else:
            notfound.append(word)
            for letter in word:
                if letter in video_directory:
                    if letter not in video_files_to_download:
                        video_files_to_download.append(letter)
                    stitch_order.append(letter)
                else:
                    notfound.append(letter)
    print(video_files_to_download, stitch_order, notfound)
    # Parallelize the video download
    download_video_parallel(video_files_to_download)

    # Append video clips
    if stitch_order:
        output_file = "stitched_output.mp4"
        file_list_txt = "file_list.txt"
        with open(file_list_txt, 'w') as file_list:
            for file_name in stitch_order:
                file_list.write(f"file 'pr_{file_name}.mp4'\n")

        ffmpeg_path = ffmpeg.get_ffmpeg_exe()
        command = [ffmpeg_path,
                '-f', 'concat', 
                '-safe', '0',
                '-i', file_list_txt,
                '-an',
                '-c', 'copy',
                '-movflags','frag_keyframe+empty_moov',
                output_file
        ]
        subprocess.run(command)
        if os.path.exists(file_list_txt):
            try:
                os.remove(file_list_txt)
            except PermissionError:
                time.sleep(1)
    else:
        print("No videos found to stitch.")

    return notfound

def download_video_parallel(file_names):
    for file_name in file_names:
        download_video(file_name)

def download_video(file_name):
    global video_storage_folder
    global video_cache_list
    global video_directory_cache

    bucket = storage.bucket()
    if not os.path.exists(f'pr_{file_name}.mp4'):
        blob = bucket.blob(f'{video_storage_folder}/{video_directory_cache[file_name]}')
        blob.download_to_filename(f'{file_name}.mp4')

        #proccessing the downloaded video
        resolution = "1440:1440" if video_storage_folder != 'videos' else "1280:720"
        process_video(f'{file_name}.mp4',f'pr_{file_name}.mp4', resolution = resolution)
        video_cache_list.append(f'pr_{file_name}')

def delete_video_parallel(file_names):
    for file_name in file_names:
        delete_video(file_name)

def delete_video(file_name):
    if os.path.exists(f'{file_name}.mp4'):
        try:
            os.remove(f'{file_name}.mp4')
        except PermissionError:
            time.sleep(1)

def process_video(input_file, output_file, frame_rate = 30, resolution = "1280:720", codec = "libx264"):
    ffmpeg_path = ffmpeg.get_ffmpeg_exe()
    command = [ffmpeg_path,
               "-i", input_file,
               "-filter:v", f"fps={frame_rate},scale={resolution}",
               "-c:v", codec,
               "-preset", "faster",
               output_file]
    if os.path.exists(input_file):
        subprocess.run(command)
        try:
            os.remove(input_file)
        except PermissionError:
            time.sleep(1)

def generate_video_stream(path):
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(8192); #8 KB chunks
            if not chunk:
                break
            yield chunk


if __name__ == '__main__':
    app.run(host = '0.0.0.0', port = 8000)
