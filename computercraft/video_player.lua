-- video_player.lua
--
-- Plays .ccvid videos (produced by the Python converter) on a monitor +
-- speaker. Video is synchronised to the audio clock so it can't drift out
-- of sync over a long playback, even on a laggy server.
--
-- Usage:
--   video_player                  -- shows a menu of videos found in VIDEOS_DIR
--   video_player <path>           -- plays a specific .ccvid file directly
--
-- Recommended build: an 8-wide x 6-tall monitor block cluster with the
-- default text scale (1) gives *exactly* 70x40 characters, which is why
-- the converter defaults to that size. See the README for details.
--
-- While a video is playing, press Q or space to stop early.

-- ===========================================================================
-- Configuration -- edit these to match your build.
-- ===========================================================================
local SPEAKER_SIDE = "top"
local MONITOR_SIDE = "right"
local MODEM_SIDE = nil          -- nil = auto-detect (recommended); only needed for networked audio
local VIDEOS_DIR = "videos"     -- folder (relative to this program) to scan for .ccvid files
local TEXT_SCALE = 0.5            -- 1 => exactly 140x80 chars on an 8x6-block monitor

-- ===========================================================================
-- Setup
-- ===========================================================================

local programDir = (shell and fs.getDir(shell.getRunningProgram())) or ""
local ccvid = dofile(fs.combine(programDir, "ccvid.lua"))
local ccnet = dofile(fs.combine(programDir, "ccnet.lua"))

local function findPeripheral(kind, preferredSide)
    if preferredSide and peripheral.getType(preferredSide) == kind then
        return peripheral.wrap(preferredSide)
    end
    return peripheral.find(kind)
end

local speaker = findPeripheral("speaker", SPEAKER_SIDE)
local monitor = findPeripheral("monitor", MONITOR_SIDE)

if not monitor then
    error("No monitor found (expected one on the '" .. MONITOR_SIDE ..
          "' side -- edit MONITOR_SIDE at the top of video_player.lua if yours is elsewhere).", 0)
end
if not speaker then
    print("Note: no local speaker found (expected on the '" .. SPEAKER_SIDE ..
          "' side). Videos with embedded audio will play silently, but " ..
          "networked audio (a separate audio computer) still works fine.")
end

local dfpwmOk, dfpwm = pcall(require, "cc.audio.dfpwm")
if not dfpwmOk then
    dfpwm = nil
    print("Note: cc.audio.dfpwm is unavailable on this CC: Tweaked version. Playing without audio.")
end

monitor.setTextScale(TEXT_SCALE)
local monW, monH = monitor.getSize()

-- ===========================================================================
-- Menu
-- ===========================================================================

local function listVideos()
    local dir = fs.combine(programDir, VIDEOS_DIR)
    local files = {}
    if fs.isDir(dir) then
        for _, name in ipairs(fs.list(dir)) do
            if name:lower():match("%.ccvid$") then
                table.insert(files, fs.combine(dir, name))
            end
        end
    end
    table.sort(files)
    return files
end

local function chooseVideo()
    local files = listVideos()
    if #files == 0 then
        error("No .ccvid files found in '" .. VIDEOS_DIR .. "'. Convert a video with the " ..
              "Python converter first and copy it into that folder.", 0)
    end

    term.clear()
    term.setCursorPos(1, 1)
    print("== CC Video Player ==")
    print("")
    print("Select a video:")
    for i, path in ipairs(files) do
        print(("  %d) %s"):format(i, fs.getName(path)))
    end
    print("")
    while true do
        io.write("> ")
        local choice = tonumber(read())
        if choice and files[choice] then
            return files[choice]
        end
        print("Invalid choice, try again.")
    end
end

-- ===========================================================================
-- Playback
-- ===========================================================================

local SAMPLE_RATE = 48000

local function playVideo(path)
    local video, err = ccvid.open(path)
    if not video then
        error("Failed to open '" .. path .. "': " .. tostring(err), 0)
    end

    -- Two distinct audio modes: legacy embedded (DFPWM baked into the
    -- .ccvid, decoded+played right here) and networked (a separate audio
    -- computer plays a companion .dfpwm file -- see audio_player.lua).
    -- The header can't tell us which was *intended* beyond "has audio":
    -- audio_length > 0 means it's embedded; audio_length == 0 means it's
    -- meant to come from the network.
    local embeddedAudio = video.hasAudio and #video.audio > 0 and speaker ~= nil and dfpwm ~= nil
    local wantsNetworkAudio = video.hasAudio and #video.audio == 0
    local networkAudioActive = false
    local audioComputerId = nil
    local videoName = fs.getName(path):gsub("%.[Cc][Cc][Vv][Ii][Dd]$", "")

    if wantsNetworkAudio then
        local modemSide, modemErr = ccnet.openModem(MODEM_SIDE)
        if not modemSide then
            print("Note: " .. modemErr .. " Playing without audio.")
        else
            local id = rednet.lookup(ccnet.PROTOCOL, ccnet.HOSTNAME)
            if not id then
                print("Note: no audio computer found on the network (looked for a computer " ..
                      "hosting '" .. ccnet.HOSTNAME .. "'). Playing without audio.")
            else
                rednet.send(id, { type = "play", name = videoName }, ccnet.PROTOCOL)
                local senderId, message = rednet.receive(ccnet.PROTOCOL, ccnet.READY_TIMEOUT)
                if senderId == id and type(message) == "table" and message.type == "ready"
                   and message.name == videoName then
                    networkAudioActive = true
                    audioComputerId = id
                elseif senderId == id and type(message) == "table" and message.type == "error" then
                    print("Note: audio computer couldn't play this video (" ..
                          tostring(message.reason) .. "). Playing without audio.")
                else
                    print("Note: audio computer didn't respond in time. Playing without audio.")
                end
            end
        end
    end

    if video.colorMode == 2 and monitor.isColor and not monitor.isColor() then
        print("Note: this monitor doesn't support color -- CC will display it in grayscale.")
    end

    local audioDesc = "no audio"
    if embeddedAudio then
        audioDesc = "with embedded audio"
    elseif networkAudioActive then
        audioDesc = "with networked audio"
    end

    term.clear()
    term.setCursorPos(1, 1)
    print(("Playing %s"):format(fs.getName(path)))
    print(("%dx%d  %.1f fps  %d frames  %.1fs  %s"):format(
        video.width, video.height, video.fps, video.frameCount,
        video.frameCount / video.fps, audioDesc))
    print("(press Q or space to stop)")

    monitor.setBackgroundColor(colors.black)
    monitor.setTextColor(colors.white)
    monitor.clear()

    local offsetX = math.max(0, math.floor((monW - video.width) / 2))
    local offsetY = math.max(0, math.floor((monH - video.height) / 2))

    -- The foreground colour and text characters never change (only the
    -- background colour encodes the picture), so precompute them once.
    local blankText = string.rep(" ", video.width)
    local blankFg = string.rep("0", video.width)

    -- audioPaced: use the sample-counted sync loop, which is only possible
    -- when audio is decoded right here (embedded mode). Networked audio
    -- (and no audio at all) fall back to plain clock pacing -- there's no
    -- way to get sample-accurate feedback from another computer without a
    -- constant stream of network chatter, and on a local wired network the
    -- two computers' clocks stay close enough together that this doesn't
    -- meaningfully drift for any normal-length video.
    local audioPaced = embeddedAudio

    local sync = { samplesPlayed = 0, audioDone = not embeddedAudio, startClock = os.clock() }

    local function audioTask()
        if not embeddedAudio then return end
        local decoder = dfpwm.make_decoder()
        local data = video.audio
        local CHUNK = 16 * 1024
        for i = 1, #data, CHUNK do
            local chunk = data:sub(i, i + CHUNK - 1)
            local buffer = decoder(chunk)
            while not speaker.playAudio(buffer) do
                os.pullEvent("speaker_audio_empty")
            end
            sync.samplesPlayed = sync.samplesPlayed + #buffer
        end
        sync.audioDone = true
    end

    local function videoTask()
        for i = 1, video.frameCount do
            if audioPaced then
                local targetSample = math.floor((i - 1) * SAMPLE_RATE / video.fps)
                while sync.samplesPlayed < targetSample and not sync.audioDone do
                    sleep(0.01)
                end
            else
                local target = sync.startClock + (i - 1) / video.fps
                while os.clock() < target do
                    sleep(0.01)
                end
            end

            local rows, ferr = ccvid.nextFrame(video)
            if not rows then
                if ferr then print("\nFrame read error: " .. ferr) end
                break
            end

            for y = 1, video.height do
                monitor.setCursorPos(1 + offsetX, 1 + offsetY + y - 1)
                monitor.blit(blankText, blankFg, rows[y])
            end
        end
    end

    local function playback()
        parallel.waitForAll(audioTask, videoTask)
    end

    local function waitForStop()
        while true do
            local event, p1 = os.pullEvent()
            if event == "key" and (p1 == keys.q or p1 == keys.space) then
                return
            elseif event == "terminate" then
                return
            end
        end
    end

    local stoppedEarly = true
    parallel.waitForAny(
        function() playback(); stoppedEarly = false end,
        waitForStop
    )

    if speaker then speaker.stop() end
    if networkAudioActive then
        rednet.send(audioComputerId, { type = "stop", name = videoName }, ccnet.PROTOCOL)
    end
    ccvid.close(video)

    print(stoppedEarly and "\nPlayback stopped." or "\nPlayback finished.")
end

-- ===========================================================================
-- Entry point
-- ===========================================================================

local cliArgs = { ... }
local path = cliArgs[1]

if not path then
    path = chooseVideo()
elseif not fs.exists(path) then
    local candidate = fs.combine(programDir, VIDEOS_DIR .. "/" .. path)
    if fs.exists(candidate) then
        path = candidate
    else
        error("File not found: " .. path, 0)
    end
end

playVideo(path)
