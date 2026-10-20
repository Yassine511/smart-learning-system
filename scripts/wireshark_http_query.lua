-- Session-scoped compatibility decoder for Wireshark versions that label
-- RFC 10008 QUERY requests as HTTP Continuation. Original packet bytes stay
-- unchanged. Sequential QUERY body segments are collected for port 8080.
-- Use Follow TCP Stream for out-of-order/partial captures. This is a lab decoder,
-- not a replacement for Wireshark's general HTTP dissector.
local query = Proto("http_query", "HTTP QUERY (compatibility decoder)")
local method = ProtoField.string("http_query.method", "Request Method")
local uri = ProtoField.string("http_query.uri", "Request URI")
local request_line = ProtoField.string("http_query.request_line", "Request line")
local content_type = ProtoField.string("http_query.content_type", "Content-Type")
local content_length = ProtoField.uint32("http_query.content_length", "Content-Length", base.DEC)
local request_in = ProtoField.framenum("http_query.request_in", "Request in frame", base.NONE, frametype.REQUEST)
local response_in = ProtoField.framenum("http_query.response_in", "Response in frame", base.NONE, frametype.RESPONSE)
local elapsed = ProtoField.double("http_query.time", "Time since request (seconds)")
local header_in = ProtoField.framenum("http_query.header_in", "Headers in frame", base.NONE, frametype.REQUEST)
query.fields = {method, uri, request_line, content_type, content_length, request_in, response_in, elapsed, header_in}
local payload_field = Field.new("tcp.payload")
local stream_field = Field.new("tcp.stream")
local retransmission = Field.new("tcp.analysis.retransmission")
local streams, frames = {}, {}

function query.init()
    streams, frames = {}, {}
end

function query.dissector(_buffer, pinfo, tree)
    local payload, stream = payload_field(), stream_field()
    if not payload or not stream then return end
    local data = payload.range:bytes():raw()
    local stream_id = tostring(stream)
    local number = pinfo.number
    local verb, path = data:match("^([A-Z]+) ([^ ]+) HTTP/1%.[01]\r\n")
    local status = data:match("^HTTP/1%.[01] (%d%d%d)")
    if not pinfo.visited and not frames[number] and not retransmission() then
        streams[stream_id] = streams[stream_id] or {queue = {}, head = 1}
        local state = streams[stream_id]
        if verb then
            local entry = {number = number, verb = verb, path = path, line = data:match("^([^\r\n]+)"),
                media = data:match("\r\n[Cc]ontent%-[Tt]ype:%s*([^\r\n]+)"),
                length = tonumber(data:match("\r\n[Cc]ontent%-[Ll]ength:%s*(%d+)")),
                time = tonumber(pinfo.abs_ts)}
            local ending = data:find("\r\n\r\n", 1, true)
            if verb == "QUERY" and ending then
                entry.body = data:sub(ending + 4)
                if #entry.body < (entry.length or 0) then state.body = entry end
            end
            table.insert(state.queue, entry)
            frames[number] = entry
        elseif status and tonumber(status) >= 200 then
            local entry = state.queue[state.head]
            if entry then
                state.head = state.head + 1
                entry.response = number
                frames[number] = {request = entry, time = tonumber(pinfo.abs_ts) - entry.time}
            end
        elseif state.body and pinfo.dst_port == 8080 then
            local entry = state.body
            entry.body = entry.body .. data
            frames[number] = {body_for = entry}
            if #entry.body >= entry.length then
                entry.body_frame = number
                state.body = nil
            end
        end
    end
    local frame = frames[number]
    local entry = frame and (frame.body_for or (frame.verb == "QUERY" and frame))
    if entry then
        local node = tree:add(query, payload.range, "Hypertext Transfer Protocol: QUERY (compatibility decoder)")
        node:add(request_line, entry.line)
        node:add(method, "QUERY")
        node:add(uri, entry.path)
        if entry.media then node:add(content_type, entry.media) end
        if entry.length then node:add(content_length, entry.length) end
        if number ~= entry.number then node:add(header_in, entry.number):set_generated() end
        if entry.response then node:add(response_in, entry.response):set_generated() end
        if entry.body and entry.media and entry.media:match("^application/json") and #entry.body == entry.length
            and (not entry.body_frame or entry.body_frame == number) then
            local body = ByteArray.new(entry.body, true):tvb("Captured QUERY JSON body")
            Dissector.get("json"):call(body, pinfo, tree)
        end
        pinfo.cols.protocol:clear_fence()
        pinfo.cols.info:clear_fence()
        pinfo.cols.protocol:set("HTTP/QUERY")
        pinfo.cols.info:set(entry.line)
    elseif status then
        local info = frames[number]
        if info and info.request and info.request.path == "/api/documents/search" then
            local node = tree:add(query, info.request.verb .. " response association (compatibility decoder)")
            node:add(method, info.request.verb):set_generated()
            node:add(uri, info.request.path):set_generated()
            node:add(request_in, info.request.number):set_generated()
            node:add(elapsed, info.time):set_generated()
        end
    end
end

register_postdissector(query, true)
