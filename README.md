# CC Video Player
A video player for ComputerCraft: Tweaked

<img width="1920" height="1080" alt="2026-08-01_15 08 01" src="https://github.com/user-attachments/assets/d4ff7134-6b38-4c31-9b0b-50baee678e66" />

This project is a showcase of how graphics in the Minecraft ComputerCraft: Tweaked mod can be used to create working videos.
# How To Use
This system is quite intuitive, even for the average person, to use.
<br>
There are two parts to the system: the converter to convert videos to .ccvid and .dfpwm (.ccvid is the video, .dfpwm is the audio), and the actual video/audio playing files stored on two computers (one plays videos on a monitor, the other plays audio through a speaker).
## Converter
The converter runs <b>ENTIRELY</b> on Python (version 3.0 or later), as well as FFmpeg/FFprobe, preferably the newer releases. Without these, the system WILL NOT function. Please, if you haven't already, download these onto your system's PATH, followed by these libraries using the `pip install (library here)` command in any terminal with a working Python install: numpy, tkinter (only have to install tkinter on SOME oses, mostly linux ones, that don't ship with tkinter already in Python, which can and does happen)
<br>
<br>
The converter can do 40 FPS max; keep in mind, however, since CC operates on Minecraft's tick speed limit of 20 ticks per second, the monitors have only a refresh rate of 20 hertz. 40 FPS can still be emulated, and even actually done with certain tools that can increase the tick rate of Minecraft, but otherwise, the actual refresh rate is capped at 20hz. The standard resolution for the converter is 154x88 pixels, and I will touch on how to setup the monitor (along with everything else) later down. KEEP IN MIND: Conversion can take an extremely long time! Be patient: if you are exporting video + audio AND the video is 1080p or more and over a minute long, it will take a long time to convert, but it is possible; you just have to be patient. The converter has multiple options for your convenience: color modes, audio checkbox, resolution customization, dithering modes, and more.
## In-Game Setup and Players
There are two main Lua scripts: `video_player.lua` and `audio_player.lua` as well as other needed scripts. These two player scripts are stored on SEPARATE computers, connected by either a wired or wireless modem. The image at the top of this README shows a basic setup in-game. By default, the computers have to be like this: place down the first (video) computer, and then anywhere else where modems can connect, place the second one. Attach a wired or wireless modem (preferably wired) to one computer, and to the other. Connect both modems with a networking cable very carefully while crouch-clicking. <b>THEN, very, VERY carefully, follow these instructions:</b> Click the video computer's modem FIRST, and THEN, click the audio computer's modem. This will connect them both to the network, with the audio computer being the next computer in the network array so the video computer can detect it. For example, if the video computer is computer_0, the audio one should be computer_1.
<br>
Now, connect, on the RIGHT side of the video computer, an 8x6 monitor, the biggest possible in CC. 8 blocks wide, 6 blocks tall. Now, on the BOTTOM of the audio computer, connect a speaker. The block setup is complete! Installation details below.
# Installation
To install: download the SOURCE CODE at the top of the repo. Unzip the .zip, drag the `converter` folder anywhere out of the unzipped root folder, maybe somewhere like the desktop, or videos folder. `cc_video_converter.py` is the file to open to find the converter GUI. For the in-game setup: go to your Minecraft world folder where the computer ids are stored (specifically `My World/computercraft/computer/`). To find out your computer ids, go to each computer and type in `id` and hit enter. Remember each number. Back to the computer folder, if you see no id numbers in there, create a folder simply with the number your computer id is, for example, if the id is #0, make a folder simply named `0`. Repeat for audio computer's id.
<br>
Create a `videos` folder inside both computer id's folders. Now, drag `audio_player.lua` to the audio computer id folder, and drag `video_player.lua` to the video computer id folder. Put `ccvid.lua` in video computer's folder as well. Copy `ccnet.lua` and paste it inside both computer's id folders. You are done! To play videos, first convert through the converter, get the .zip from wherever you exported it to, unzip it, drag the .ccvid file to the videos folder inside the video computer folder and the .dfpwm file to the audio computer's videos folder.
<br>
TO SETUP IN GAME: Go to the audio computer, run the audio program and keep it running. Now go to the video computer, run the video program. Using each listed number, select which video you want to play by typing the number. Sit back and enjoy the video!
# NOTICE
This project utilizes the MIT License. If distributing, commercializing (rather you to not but you can), or forking my work, you MUST first read and utilize that exact copy of the License.
