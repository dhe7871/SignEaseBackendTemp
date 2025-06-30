from flask import Flask, request, jsonify
from flask_cors import CORS
from pydantic import BaseModel, ValidationError
import firebase_admin
from firebase_admin import storage
import os
import time
import secrets
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import threading

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

#configuration for email
emailAddress = "signease.kgp@gmail.com"
emailPassword = "<Your-App-Password-for-Gmail>"

#caching user data and other global variables
users = None
newUser = None
loggedInDict = {}
OTP = None
OTPValidationFor = None
uidForPasswordChange = None

#Firebase Initialization
cred = firebase_admin.credentials.Certificate("aeroweb27-firebase-adminsdk-oudwq-40e248fbf8.json")
firebase_admin.initialize_app(cred, {
    'storageBucket': 'aeroweb27.appspot.com'
})

#Data models using Pydantic
class User(BaseModel):
    name: str
    username: str
    email: str
    password: str

class logUser(BaseModel):
    id: str
    password: str
    cuid: str

class logOutUser(BaseModel):
    cuid: str

class otp(BaseModel):
    OTP: int

class forgotPass(BaseModel):
    email: str

class newPassword(BaseModel):
    password: str

#Firebase Functions to Download and Upload
def uploadFile(fileName, serverFilePath):
    bucket = storage.bucket()
    blob = bucket.blob(serverFilePath)
    blob.upload_from_filename(fileName)

def downloadFile(serverFilePath, fileName):
    if fileName in os.listdir(os.getcwd()):
        try:
            os.remove(fileName)
        except PermissionError:
            time.sleep(1)
    bucket = storage.bucket()
    blob = bucket.blob(serverFilePath)
    blob.download_to_filename(fileName)

def cachingUserData():
    global users, loggedInDict
    if not os.path.exists('users.json'):
        downloadFile('users/users.json', 'users.json')
    with open('users.json', 'r') as userFile:
        users = json.load(userFile)
    for uid in users:
        loggedInDict[uid] = False

def backgroundTask():
    while True:
        uploadFile('users.json', 'users/users.json')
        print(f'Users data uploaded to Firebase at {time.strftime("%A, %B %d,%Y %I:%M:%S %p", time.localtime())}')
        time.sleep(3600)

#Start Background Task
taskThread = threading.Thread(target = backgroundTask)
taskThread.daemon = True
taskThread.start()

#Helper Function (Utility Functions)
def addUser(user):
    global users, loggedInDict
    uid = f'user{len(users)}'
    users[uid] = {
        'registeredat': time.strftime("%A, %B %d,%Y %I:%M:%S %p", time.localtime()),
        'loggedin': False,
        **user.dict()
    }
    loggedInDict[uid] = False
    with open('users.json', 'w') as usersFile:
        json.dump(users, usersFile, indent = 4)

def generateOTP():
    return secrets.randbelow(900000) + 100000

def sendMail(toAddress, subject, body):
    smtpServer = 'smtp.gmail.com'
    smtpPort = 587
    msg  =MIMEMultipart()
    msg['From'] = emailAddress
    msg['To'] = toAddress
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    try:
        with smtplib.SMTP(smtpServer, smtpPort) as server:
            server.starttls()
            server.login(emailAddress, emailPassword)
            server.sendmail(emailAddress, toAddress, msg.as_string())
        print('Email sent successfully.')
    except Exception as e:
        print(f'Error sending email: {e}')

cachingUserData()

#API Endpoints
@app.route('/signUp/', methods = ['POST'])
def signUp():
    global newUser, OTP, OTPValidationFor
    data = request.get_json()
    try:
        newuser = User(**data)
    except ValidationError as e:
        return jsonify(e.errors()), 400
    
    for uid in users:
        if users[uid]['username'] == newuser.username:
            return jsonify(detail = 'Username already exists.'), 400
        if users[uid]['email'] == newuser.email:
            return jsonify(detail = 'Email already in use.'), 400
        
    newUser = newuser
    OTP = generateOTP()
    OTPValidationFor = 'signup'
    sendMail(newuser.email, 'SignEase', f'Your OTP for SignEase is {OTP}.')
    return jsonify(detail = f'OTP has been sent to {newuser.email}')

@app.route('/logIn/', methods = ['POST'])
def logIn():
    global loggedInDict
    data = request.get_json()
    try:
        loguser = logUser(**data)
    except ValidationError as e:
        return jsonify(e.errors()), 400
    logKey = 'username' if '@' not in loguser.id else 'email'
    for uid, user in users.items():
        if user[logKey] == loguser.id:
            if user['password'] == loguser.password:
                if loguser.cuid in loggedInDict:
                    loggedInDict[loguser.cuid] = False
                loggedInDict[uid] = True
                return jsonify(detail = 'Logged in Successfully...', uid = uid, name = user["name"])
            else:
                return jsonify(detail = 'Incorrect Password!'), 400

    return jsonify(detail = f"{loguser.id} doesn't exist!"), 400

@app.route('/logOut/', methods = ['POST'])
def logOut():
    global loggedInDict
    data = request.get_json()
    try:
        logoutuser = logOutUser(**data)
    except ValidationError as e:
        return jsonify(e.errors()), 400
    if logoutuser.cuid in loggedInDict:
        loggedInDict[logoutuser.cuid] = False
        return jsonify(detail = 'Logged Out Successfully...')
    elif logoutuser.cuid == '':
        return jsonify(detail = 'Please log in first to log out!'), 400
    else:
        return jsonify(detail = 'uid not found for which you want to log out!'), 404
    
@app.route('/resendOTP/', methods = ['GET'])
def resendOTP():
    global OTP, newUser, uidForPasswordChange
    if OTP:
        if newUser:
            OTP = generateOTP()
            sendMail(newUser.email, 'SignEase', f'Your OTP for SignEase is {OTP}.')
            return jsonify(detail = f'OTP has been sent to {newUser.email}.')
        elif uidForPasswordChange:
            OTP = generateOTP()
            sendMail(users[uidForPasswordChange]['email'], 'SignEase', f'Your OTP for SignEase is {OTP}.')
            return jsonify(detail = f'OTP has been sent to {users[uidForPasswordChange]['email']}')
    else:
        return jsonify(detail = 'Please generate the OTP first!'), 404

@app.route('/validateOTP/', methods = ['POST'])
def validateOTP():
    global OTP, newUser, OTPValidationFor
    data = request.get_json()
    try:
        OTPtoValidate = otp(**data)
    except ValidationError as e:
        return jsonify(e.errors()), 400
    
    if OTP == OTPtoValidate.OTP:
        if OTPValidationFor == 'signup':
            addUser(newUser)
            newUser = None
        OTP = None
        validationFor = OTPValidationFor
        OTPValidationFor = None
        return jsonify(detail = 'OTP validatio successful.', valid = True, otpvalidationfor = validationFor)
    else:
        return jsonify(detail = 'Invalid OTP! Try Again!', valid = False, otpvalidationfor = validationFor)

@app.route('/forgotPassword/', methods = ['POST'])
def forgotPassword():
    global OTP, OTPValidationFor, uidForPasswordChange
    data = request.get_json()
    try:
        forgotpass = forgotPass(**data)
    except ValidationError as e:
        return jsonify(e.errors()), 400
    for uid, user in users.items():
        if user['email'] == forgotpass.email:
            OTP = generateOTP()
            OTPValidationFor = 'forgotpassword'
            uidForPasswordChange = uid
            sendMail(user['email'], 'SignEase', f'Your OTP for SignEase is {OTP}.')
            return jsonify(detail=f"OTP has been sent to {user['email']}")
    return jsonify(detail = 'Email not found!'), 404

@app.route('/changePassword/', methods = ['POST'])
def changePassword():
    global users, uidForPasswordChange, OTPValidationFor
    data = request.get_json()
    try:
        newpassword = newPassword(**data)
    except ValidationError as e:
        return jsonify(e.errors()), 400
    
    if uidForPasswordChange and OTPValidationFor is None:
        users[uidForPasswordChange]['password'] = newpassword.password
        with open('users.json', 'w') as userFile:
            json.dump(users, userFile, indent=4)
        uidForPasswordChange = None
        return jsonify(detail = 'Password changed successfully.')
    else:
        return jsonify(detail = 'Password cannot be changed: Either OTP is not generated or validated.'), 400
    
@app.route('/users/', methods = ['GET'])
def getUsers():
    global users, loggedInDict
    tempUsers = users.copy()
    for uid in users:
        tempUsers[uid]['loggedin'] = loggedInDict[uid]
    return jsonify(numusers = len(users) - 1, users = tempUsers)

if __name__ == '__main__':
    app.run(host = '0.0.0.0', port = 8000)
