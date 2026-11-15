"""Geohash encoding and coverage for nearby customer queries."""
import math

ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(latitude: float, longitude: float, precision: int = 9) -> str:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Invalid coordinates")
    lat, lon = [-90.0, 90.0], [-180.0, 180.0]
    result, bits, value = "", 0, 0
    even = True
    while len(result) < precision:
        interval = lon if even else lat
        point = longitude if even else latitude
        mid = (interval[0] + interval[1]) / 2
        value = (value << 1) | (point >= mid)
        if point >= mid:
            interval[0] = mid
        else:
            interval[1] = mid
        even = not even
        bits += 1
        if bits == 5:
            result += ALPHABET[value]
            bits, value = 0, 0
    return result


def nearby_prefixes(latitude: float, longitude: float, radius_km: float = 3) -> list[str]:
    """Cover the bounding box using all intersecting precision-5 geohash cells."""
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Invalid coordinates")
    lat_step, lon_step = 180 / 4096, 360 / 8192
    lat_delta = radius_km / 110.574
    min_lat = max(-90, latitude - lat_delta)
    max_lat = min(90, latitude + lat_delta)
    min_cos = max(0.000001, math.cos(math.radians(max(abs(min_lat), abs(max_lat)))))
    lon_delta = min(180, radius_km / (111.320 * min_cos))
    min_y = max(0, int((min_lat + 90) / lat_step))
    max_y = min(4095, int((max_lat + 90) / lat_step))
    min_x = math.floor((longitude - lon_delta + 180) / lon_step)
    max_x = math.floor((longitude + lon_delta + 180) / lon_step)
    if (max_x - min_x + 1) * (max_y - min_y + 1) > 500:
        raise ValueError("Location is outside the supported search area")
    return sorted({
        encode(-90 + (y + 0.5) * lat_step, -180 + ((x % 8192) + 0.5) * lon_step, 5)
        for y in range(min_y, max_y + 1)
        for x in range(min_x, max_x + 1)
    })
