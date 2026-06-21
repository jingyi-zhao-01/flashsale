-- repair_negative_counter_and_incr
local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local current = redis.call("GET", key)
if current and tonumber(current) < 0 then
    redis.call("DEL", key)
end
local counter = redis.call("INCR", key)
if counter == 1 then
    redis.call("EXPIRE", key, ttl)
end
return counter
