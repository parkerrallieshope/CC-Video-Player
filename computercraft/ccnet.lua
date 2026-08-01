-- ccnet.lua
--
-- Shared rednet constants + helpers for the two-computer video/audio setup.
-- Both video_player.lua (video computer) and audio_player.lua (audio
-- computer) dofile this, so they always agree on the protocol/hostname
-- without those constants needing to be hand-copied into two files.
--
-- The audio computer calls rednet.host(PROTOCOL, HOSTNAME) once at startup;
-- the video computer calls rednet.lookup(PROTOCOL, HOSTNAME) to find it.
-- This means neither computer needs to know the other's computer ID ahead
-- of time -- it's discovered by name over whatever modems/network cables
-- connect them, wired or wireless, and keeps working if you rebuild in a
-- new world.

local ccnet = {}

ccnet.PROTOCOL = "ccvideosuite"
ccnet.HOSTNAME = "ccvideo_speaker"
ccnet.READY_TIMEOUT = 3    -- seconds to wait for the audio computer to confirm it's ready

--- Find the side a modem peripheral is attached to. If preferredSide is
-- given and actually has a modem, that's returned immediately; otherwise
-- every side is checked and the first modem found is used. Returns nil if
-- no modem is attached anywhere.
function ccnet.findModemSide(preferredSide)
    if preferredSide and peripheral.getType(preferredSide) == "modem" then
        return preferredSide
    end
    for _, name in ipairs(peripheral.getNames()) do
        if peripheral.getType(name) == "modem" then
            return name
        end
    end
    return nil
end

--- Find a modem and open rednet on it. Returns the side opened on, or
-- nil+errorMessage if none was found.
function ccnet.openModem(preferredSide)
    local side = ccnet.findModemSide(preferredSide)
    if not side then
        return nil, "No modem found. Attach a wired or wireless modem to this computer."
    end
    if not rednet.isOpen(side) then
        rednet.open(side)
    end
    return side
end

return ccnet
