-- ccvid.lua
--
-- Reader/decoder for the .ccvid container format produced by the Python
-- converter (converter/cc_video_converter.py). Handles the binary header,
-- the embedded DFPWM1a audio blob, and per-frame RLE/raw pixel decoding.
--
-- This module deliberately knows nothing about peripherals (speakers,
-- monitors) -- see video_player.lua for the part that actually plays a
-- video. Keeping the format logic separate makes both halves easier to
-- read, test, and reuse (e.g. in a different front-end).
--
-- File format is documented in detail in converter/video_format.py and
-- docs/FORMAT.md; the short version:
--
--   header (19 bytes, little-endian):
--     "CCV1" version:B width:B height:B fpsX100:I2 colorMode:B hasAudio:B
--     frameCount:I4 audioLength:I4
--   audio blob: audioLength bytes of raw DFPWM1a
--   frames: frameCount records of [len:I2][flag:B][payload...]

local ccvid = {}

local MAGIC = "CCV1"
local VERSION = 1
local HEADER_SIZE = 19

-- Palette: array index (color value + 1) -> blit hex digit, brightness ascending.
local PALETTES = {
    [0] = { "f", "0" },              -- 1-bit: black, white
    [1] = { "f", "7", "8", "0" },    -- 2-bit: black, gray, lightGray, white
    -- 4-bit: full 16-color CC palette. Index N -> blit digit N directly
    -- (matches colors.toBlit()'s own ordering), so no remapping needed.
    -- Only shows real color on an *Advanced* monitor -- CC itself falls
    -- back to grayscale automatically on standard monitors.
    [2] = { "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f" },
}
local BITS_PER_PIXEL = { [0] = 1, [1] = 2, [2] = 4 }

--- Open a .ccvid file and parse its header + audio blob.
-- Returns a `video` table on success, or nil+errorMessage on failure.
-- The returned table keeps the file handle open (positioned at the start
-- of frame data) until ccvid.close() is called.
function ccvid.open(path)
    if not fs.exists(path) then
        return nil, "File not found: " .. path
    end

    local handle, openErr = fs.open(path, "rb")
    if not handle then
        return nil, "Could not open '" .. path .. "': " .. tostring(openErr)
    end

    local headerData = handle.read(HEADER_SIZE)
    if not headerData or #headerData < HEADER_SIZE then
        handle.close()
        return nil, "File is too short to contain a valid .ccvid header"
    end

    local ok, magic, version, width, height, fpsX100, colorMode, hasAudio, frameCount, audioLength =
        pcall(string.unpack, "<c4BBBI2BBI4I4", headerData)

    if not ok then
        handle.close()
        return nil, "Malformed header: " .. tostring(magic)
    end
    if magic ~= MAGIC then
        handle.close()
        return nil, "Not a .ccvid file (bad magic bytes)"
    end
    if version ~= VERSION then
        handle.close()
        return nil, "Unsupported .ccvid version " .. tostring(version) .. " (expected " .. VERSION .. ")"
    end
    if not PALETTES[colorMode] then
        handle.close()
        return nil, "Unknown color mode " .. tostring(colorMode)
    end

    local audioData = ""
    if hasAudio == 1 and audioLength > 0 then
        audioData = handle.read(audioLength)
        if not audioData or #audioData < audioLength then
            handle.close()
            return nil, "File is truncated (expected " .. audioLength .. " bytes of audio)"
        end
    end

    return {
        _handle = handle,
        _framesRead = 0,
        path = path,
        width = width,
        height = height,
        fps = fpsX100 / 100,
        colorMode = colorMode,
        hasAudio = hasAudio == 1,
        frameCount = frameCount,
        audio = audioData,
        palette = PALETTES[colorMode],
        bpp = BITS_PER_PIXEL[colorMode],
    }
end

--- Close the underlying file handle. Safe to call more than once.
function ccvid.close(video)
    if video._handle then
        video._handle.close()
        video._handle = nil
    end
end

-- Decode a row-wise RLE payload into an array of `height` background-colour
-- strings (each `width` blit hex chars long).
local function decodeRle(payload, width, height, bpp, palette)
    local shift = 8 - bpp
    local runMask = bit32.rshift(255, bpp)
    local rows = {}
    local pos = 1 -- 1-indexed cursor into payload

    for y = 1, height do
        local pieces = {}
        local nPieces = 0
        local produced = 0
        while produced < width do
            local b = string.byte(payload, pos)
            pos = pos + 1
            local color = bit32.rshift(b, shift)
            local runLen = bit32.band(b, runMask) + 1
            nPieces = nPieces + 1
            pieces[nPieces] = string.rep(palette[color + 1], runLen)
            produced = produced + runLen
        end
        rows[y] = table.concat(pieces)
    end
    return rows
end

-- Decode a raw (fixed-size, bit-packed) payload into an array of `height`
-- background-colour strings.
local function decodeRaw(payload, width, height, bpp, palette)
    local mask = bit32.rshift(255, 8 - bpp) -- e.g. bpp=1 -> 1, bpp=2 -> 3
    local rows = {}
    local acc = 0
    local nbits = 0
    local bytePos = 1

    for y = 1, height do
        local chars = {}
        for x = 1, width do
            while nbits < bpp do
                acc = bit32.bor(bit32.lshift(acc, 8), string.byte(payload, bytePos))
                bytePos = bytePos + 1
                nbits = nbits + 8
            end
            nbits = nbits - bpp
            local val = bit32.band(bit32.rshift(acc, nbits), mask)
            chars[x] = palette[val + 1]
        end
        rows[y] = table.concat(chars)
    end
    return rows
end

--- Read and decode the next frame. Returns an array of `video.height`
-- background-colour strings (ready to pass as the 3rd argument to
-- monitor.blit), or nil at end-of-stream, or nil+errorMessage on failure.
function ccvid.nextFrame(video)
    if video._framesRead >= video.frameCount then
        return nil
    end

    local lenData = video._handle.read(2)
    if not lenData or #lenData < 2 then
        return nil, "Unexpected end of file while reading a frame length"
    end
    local recLen = string.unpack("<I2", lenData)

    local body = video._handle.read(recLen)
    if not body or #body < recLen then
        return nil, "Unexpected end of file while reading a frame body"
    end

    video._framesRead = video._framesRead + 1

    local flag = string.byte(body, 1)
    local payload = body:sub(2)

    if flag == 1 then
        return decodeRle(payload, video.width, video.height, video.bpp, video.palette)
    elseif flag == 0 then
        return decodeRaw(payload, video.width, video.height, video.bpp, video.palette)
    end
    return nil, "Unknown frame flag " .. tostring(flag) .. " (corrupt file?)"
end

return ccvid
