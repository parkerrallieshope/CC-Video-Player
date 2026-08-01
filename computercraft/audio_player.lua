-- audio_player.lua
--
-- Runs on a dedicated "audio computer" alongside a main "video computer"
-- running video_player.lua. Waits for play/stop commands over rednet and
-- plays the matching .dfpwm file through a local speaker.
--
-- WHY A SECOND COMPUTER: decoding DFPWM audio and rendering ~40
-- monitor.blit calls a frame on the *same* computer competes for the same
-- CPU budget, and on anything but a short/simple clip that shows up as
-- dropped video frames or choppy audio. Splitting video and audio onto two
-- computers connected by a wired (or wireless) modem fixes that, since
-- each one only has to do one job.
--
-- SETUP: put this file (and ccnet.lua) on a second computer with a speaker
-- attached and a modem connected to the same network as the video
-- computer's modem. Put your .dfpwm files (matching the .ccvid filenames
-- on the video computer) in a "videos" folder next to this file. Then set
-- this program to run automatically:
--   1. Run once manually to check it starts cleanly: audio_player
--   2. Make it persistent: copy this file's contents into "startup.lua"
--      on this computer (or `edit startup.lua` and add `shell.run("audio_player")`),
--      so it's always running and ready after a server restart.
-- See the guide at https://tweaked.cc/guide/startup.html for more on
-- startup programs.
--
-- No configuration should be needed on the video computer's end beyond
-- both computers being on the same rednet network -- see ccnet.lua for how
-- they find each other (by hostname, not computer ID, so this works
-- unchanged even if you rebuild everything in a new world).

-- ===========================================================================
-- Configuration -- edit these to match your build.
-- ===========================================================================
local SPEAKER_SIDE = "bottom"
local MODEM_SIDE = nil          -- nil = auto-detect (recommended)
local VIDEOS_DIR = "videos"

-- ===========================================================================
-- Setup
-- ===========================================================================

local programDir = (shell and fs.getDir(shell.getRunningProgram())) or ""
local ccnet = dofile(fs.combine(programDir, "ccnet.lua"))

local function findPeripheral(kind, preferredSide)
    if preferredSide and peripheral.getType(preferredSide) == kind then
        return peripheral.wrap(preferredSide)
    end
    return peripheral.find(kind)
end

local speaker = findPeripheral("speaker", SPEAKER_SIDE)
if not speaker then
    error("No speaker found (expected one on the '" .. SPEAKER_SIDE ..
          "' side -- edit SPEAKER_SIDE at the top of audio_player.lua if yours is elsewhere).", 0)
end

local dfpwmOk, dfpwm = pcall(require, "cc.audio.dfpwm")
if not dfpwmOk then
    error("cc.audio.dfpwm is unavailable on this CC: Tweaked version -- can't play audio.", 0)
end

local modemSide, modemErr = ccnet.openModem(MODEM_SIDE)
if not modemSide then
    error(modemErr, 0)
end
rednet.host(ccnet.PROTOCOL, ccnet.HOSTNAME)

print("== CC Video Suite: audio computer ==")
print("Speaker: ok (" .. SPEAKER_SIDE .. ")")
print("Modem: ok (" .. modemSide .. "), hosting as '" .. ccnet.HOSTNAME .. "'")
print("Waiting for a video...")

-- ===========================================================================
-- Playback
-- ===========================================================================

local function playFile(name, path, senderId)
    local handle = fs.open(path, "rb")
    local data = handle.readAll()
    handle.close()

    local decoder = dfpwm.make_decoder()
    local CHUNK = 16 * 1024

    -- Decode+submit the first chunk BEFORE telling the video computer we're
    -- ready. This means "ready" lines up with sound actually starting,
    -- rather than with merely having confirmed the file exists -- the file
    -- load + decode above happens while the video computer is still
    -- waiting, instead of sitting as an invisible gap *after* it starts
    -- its own clock. Keeps the two computers in sync without needing a
    -- manual offset for most setups (see ccnet.SYNC_OFFSET if you still
    -- need to nudge it).
    local firstLen = math.min(CHUNK, #data)
    local firstBuffer = decoder(data:sub(1, firstLen))
    while not speaker.playAudio(firstBuffer) do
        os.pullEvent("speaker_audio_empty")
    end
    rednet.send(senderId, { type = "ready", name = name }, ccnet.PROTOCOL)

    local interrupted = false

    local function feedLoop()
        for i = firstLen + 1, #data, CHUNK do
            local chunk = data:sub(i, i + CHUNK - 1)
            local buffer = decoder(chunk)
            while not speaker.playAudio(buffer) do
                os.pullEvent("speaker_audio_empty")
            end
        end
    end

    local function listenForStop()
        while true do
            local sId, message = rednet.receive(ccnet.PROTOCOL)
            if type(message) == "table" and message.type == "stop" and message.name == name then
                interrupted = true
                return
            end
        end
    end

    parallel.waitForAny(feedLoop, listenForStop)

    -- Only force-stop the speaker if we were actually told to (Q/space on
    -- the video computer, or it starting a different video). If feedLoop
    -- just finished normally, there's still up to a few seconds of
    -- already-submitted audio genuinely queued up waiting to play -- 
    -- calling speaker.stop() here would cut that off right before the end.
    -- Letting it be is enough; the speaker drains its own buffer on its own.
    if interrupted then
        speaker.stop()
    end
    data = nil
end

-- ===========================================================================
-- Main loop
-- ===========================================================================

while true do
    local senderId, message = rednet.receive(ccnet.PROTOCOL)
    if type(message) == "table" and message.type == "play" and type(message.name) == "string" then
        local name = message.name
        local path = fs.combine(programDir, VIDEOS_DIR .. "/" .. name .. ".dfpwm")

        if fs.exists(path) then
            print("Now playing: " .. name)
            playFile(name, path, senderId)
            print("Idle. Waiting for a video...")
        else
            print("No matching audio for '" .. name .. "' (looked for " ..
                  VIDEOS_DIR .. "/" .. name .. ".dfpwm)")
            rednet.send(senderId, { type = "error", reason = "missing_dfpwm", name = name }, ccnet.PROTOCOL)
        end
    end
end
