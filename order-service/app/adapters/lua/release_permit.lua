-- safe_release_counter
local key = KEYS[1]
local current = redis.call("GET", key)
if not current then
    return 0
end
local inflight = tonumber(current)
if inflight <= 1 then
    redis.call("DEL", key)
    return 0
end
return redis.call("DECR", key)
