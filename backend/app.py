from flask import Flask, jsonify
from supabase_client import supabase
import os
from flask import Flask, jsonify, request
app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({
        "message": "AI Traffic Violation Backend Running"
    })



@app.route("/videos")
def get_videos():
    response = (
        supabase
        .table("videos")
        .select("*")
        .execute()
    )

    return jsonify(response.data)


@app.route("/violations")
def get_violations():
    response = (
        supabase
        .table("violations")
        .select("*")
        .execute()
    )

    return jsonify(response.data)




UPLOAD_FOLDER = "uploads"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/upload-video", methods=["POST"])
def upload_video():
    print("FORM =", request.form)
    print("FILES =", request.files)

    if "video" not in request.files:
        return jsonify({
            "error": "No video file"
        }), 400

    file = request.files["video"]

    return jsonify({
        "message": file.filename
    })

    return jsonify({
        "message": "Upload successful",
        "video": response.data
    })

@app.route("/upload-video", methods=["GET"])
def upload_test():
    return {
        "message": "upload route exists"
    }

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )