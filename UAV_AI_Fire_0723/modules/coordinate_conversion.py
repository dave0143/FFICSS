# coordinate_conversion.py - 新增座標轉換模組
"""
座標轉換模組
提供GPS、角度、距離等各種座標轉換功能
包含向量交疊計算、目標位置計算等高級功能
"""

import math
from typing import Dict, Any, Optional, List, Tuple


class CoordinateConversionModule:
    """座標轉換模組類別"""
    
    def __init__(self, logger):
        self.logger = logger
        
        # 地球半徑常數 (公尺)
        self.EARTH_RADIUS = 6378137.0
        
        # 座標轉換常數
        self.METERS_PER_DEGREE_LAT = 111000.0  # 1度緯度約等於111公里
        
    def log(self, msg, level="info"):
        """記錄日誌"""
        if self.logger:
            self.logger.log(f"[CoordConv] {msg}", step="coordinate_conversion", level=level)
    
    def calculate_spray_path(self, start_gps: Dict[str, float], target_position: Dict[str, float], 
                           params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        計算噴灑路徑
        
        Args:
            start_gps: 起始GPS位置
            target_position: 目標位置
            params: 參數包含waypoint_num, flight_speed, loiter_time等
            
        Returns:
            噴灑路徑任務格式
        """
        try:
            waypoint_num = params["waypoint_num"]
            flight_speed = params["flight_speed"]
            loiter_time = params["loiter_time"]
            
            # 計算路徑點
            mission = []
            
            for i in range(waypoint_num):
                ratio = (i + 1) / waypoint_num
                
                # 線性插值計算路徑點
                waypoint_lat = start_gps["lat"] + (target_position["lat"] - start_gps["lat"]) * ratio
                waypoint_lon = start_gps["lon"] + (target_position["lon"] - start_gps["lon"]) * ratio
                waypoint_alt = start_gps["alt"] + (target_position["alt"] - start_gps["alt"]) * ratio
                
                # 計算朝向角度 (指向目標)
                angle = self._calculate_bearing(start_gps, target_position)
                
                waypoint = {
                    "waypoint": i + 1,
                    "lat": waypoint_lat,
                    "lon": waypoint_lon,
                    "alt": waypoint_alt,
                    "ang": angle,
                    "flight_speed": flight_speed,
                    "loiter_time": loiter_time
                }
                
                mission.append(waypoint)
            
            result = {
                "task": "process",
                "mission": mission
            }
            
            self.log(f"計算噴灑路徑完成: {waypoint_num}個路徑點")
            return result
            
        except Exception as e:
            self.log(f"計算噴灑路徑錯誤: {e}", "error")
            return None
    
    def calculate_vector_intersection(self, uav1_gps: Dict[str, float], gimbal1: Dict[str, Any],
                                    uav2_gps: Dict[str, float], gimbal2: Dict[str, Any]) -> Optional[Dict[str, float]]:
        """
        計算兩個GPS+yaw,pitch向量的交疊點
        
        Args:
            uav1_gps: 第一個UAV位置
            gimbal1: 第一個雲台資訊
            uav2_gps: 第二個UAV位置
            gimbal2: 第二個雲台資訊
            
        Returns:
            交疊點位置或None
        """
        try:
            # 轉換為3D向量
            pos1 = self._gps_to_cartesian(uav1_gps)
            pos2 = self._gps_to_cartesian(uav2_gps)
            
            # 計算方向向量
            dir1 = self._gimbal_to_direction_vector(gimbal1)
            dir2 = self._gimbal_to_direction_vector(gimbal2)
            
            # 計算兩條直線的最近點
            intersection_point = self._calculate_line_intersection_3d(pos1, dir1, pos2, dir2)
            
            if intersection_point:
                # 轉換回GPS座標
                intersection_gps = self._cartesian_to_gps(intersection_point)
                
                self.log(f"計算向量交疊點: lat={intersection_gps['lat']:.6f}, "
                        f"lon={intersection_gps['lon']:.6f}, alt={intersection_gps['alt']:.2f}")
                return intersection_gps
            
            return None
            
        except Exception as e:
            self.log(f"計算向量交疊點錯誤: {e}", "error")
            return None
    
    def calculate_distance(self, pos1: Dict[str, float], pos2: Dict[str, float]) -> float:
        """
        計算兩個GPS位置之間的距離
        
        Args:
            pos1: 第一個位置
            pos2: 第二個位置
            
        Returns:
            距離 (公尺)
        """
        try:
            # 使用Haversine公式計算距離
            lat1_rad = math.radians(pos1["lat"])
            lon1_rad = math.radians(pos1["lon"])
            lat2_rad = math.radians(pos2["lat"])
            lon2_rad = math.radians(pos2["lon"])
            
            dlat = lat2_rad - lat1_rad
            dlon = lon2_rad - lon1_rad
            
            a = (math.sin(dlat/2)**2 + 
                 math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon/2)**2)
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            
            # 水平距離
            horizontal_distance = self.EARTH_RADIUS * c
            
            # 垂直距離
            vertical_distance = abs(pos2["alt"] - pos1["alt"])
            
            # 3D距離
            distance = math.sqrt(horizontal_distance**2 + vertical_distance**2)
            
            return distance
            
        except Exception as e:
            self.log(f"計算距離錯誤: {e}", "error")
            return 0.0
    
    def _calculate_bearing(self, start_gps: Dict[str, float], end_gps: Dict[str, float]) -> float:
        """計算方位角"""
        try:
            lat1_rad = math.radians(start_gps["lat"])
            lat2_rad = math.radians(end_gps["lat"])
            dlon_rad = math.radians(end_gps["lon"] - start_gps["lon"])
            
            y = math.sin(dlon_rad) * math.cos(lat2_rad)
            x = (math.cos(lat1_rad) * math.sin(lat2_rad) - 
                 math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon_rad))
            
            bearing_rad = math.atan2(y, x)
            bearing_deg = math.degrees(bearing_rad)
            
            # 轉換為0-360度
            return (bearing_deg + 360) % 360
            
        except Exception as e:
            self.log(f"計算方位角錯誤: {e}", "error")
            return 0.0
    
    def _gps_to_cartesian(self, gps: Dict[str, float]) -> Tuple[float, float, float]:
        """將GPS座標轉換為笛卡爾座標"""
        lat_rad = math.radians(gps["lat"])
        lon_rad = math.radians(gps["lon"])
        alt = gps["alt"]
        
        # 簡化的平面投影 (適用於小範圍)
        x = (self.EARTH_RADIUS + alt) * math.cos(lat_rad) * math.cos(lon_rad)
        y = (self.EARTH_RADIUS + alt) * math.cos(lat_rad) * math.sin(lon_rad)
        z = (self.EARTH_RADIUS + alt) * math.sin(lat_rad)
        
        return (x, y, z)
    
    def _cartesian_to_gps(self, cartesian: Tuple[float, float, float]) -> Dict[str, float]:
        """將笛卡爾座標轉換為GPS座標"""
        x, y, z = cartesian
        
        # 計算距離地心距離
        r = math.sqrt(x**2 + y**2 + z**2)
        
        # 計算緯度
        lat_rad = math.asin(z / r)
        lat_deg = math.degrees(lat_rad)
        
        # 計算經度
        lon_rad = math.atan2(y, x)
        lon_deg = math.degrees(lon_rad)
        
        # 計算高度
        alt = r - self.EARTH_RADIUS
        
        return {
            "lat": lat_deg,
            "lon": lon_deg,
            "alt": alt
        }
    
    def _gimbal_to_direction_vector(self, gimbal: Dict[str, Any]) -> Tuple[float, float, float]:
        """將雲台角度轉換為方向向量"""
        yaw_rad = math.radians(gimbal.get("yaw", 0))
        pitch_rad = math.radians(gimbal.get("pitch", 0))
        
        # 計算方向向量 (NED座標系)
        # North, East, Down
        dx = math.cos(pitch_rad) * math.cos(yaw_rad)  # North
        dy = math.cos(pitch_rad) * math.sin(yaw_rad)  # East
        dz = math.sin(pitch_rad)  # Down
        
        return (dx, dy, dz)
    
    def _calculate_line_intersection_3d(self, pos1: Tuple[float, float, float], dir1: Tuple[float, float, float],
                                       pos2: Tuple[float, float, float], dir2: Tuple[float, float, float]) -> Optional[Tuple[float, float, float]]:
        """計算3D空間中兩條直線的最近交點"""
        try:
            # 點1和方向1
            p1 = pos1
            d1 = dir1
            
            # 點2和方向2
            p2 = pos2
            d2 = dir2
            
            # 計算兩條線的最近點
            # 使用公式: P1 + t1*D1 最接近 P2 + t2*D2
            
            # 向量計算
            w0 = (p1[0] - p2[0], p1[1] - p2[1], p1[2] - p2[2])
            
            a = self._dot_product(d1, d1)
            b = self._dot_product(d1, d2)
            c = self._dot_product(d2, d2)
            d = self._dot_product(d1, w0)
            e = self._dot_product(d2, w0)
            
            # 檢查平行線
            denom = a * c - b * b
            if abs(denom) < 1e-10:
                # 平行線，取中點
                t1 = 0.0
            else:
                t1 = (b * e - c * d) / denom
            
            t2 = (a * e - b * d) / denom if abs(denom) >= 1e-10 else 0.0
            
            # 計算兩條線上的最近點
            point1 = (p1[0] + t1 * d1[0], p1[1] + t1 * d1[1], p1[2] + t1 * d1[2])
            point2 = (p2[0] + t2 * d2[0], p2[1] + t2 * d2[1], p2[2] + t2 * d2[2])
            
            # 返回中點作為交點
            intersection = (
                (point1[0] + point2[0]) / 2,
                (point1[1] + point2[1]) / 2,
                (point1[2] + point2[2]) / 2
            )
            
            return intersection
            
        except Exception as e:
            self.log(f"計算3D直線交點錯誤: {e}", "error")
            return None
    
    def _dot_product(self, v1: Tuple[float, float, float], v2: Tuple[float, float, float]) -> float:
        """計算向量點積"""
        return v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]
    
    def _vector_magnitude(self, v: Tuple[float, float, float]) -> float:
        """計算向量大小"""
        return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
    
    def _normalize_vector(self, v: Tuple[float, float, float]) -> Tuple[float, float, float]:
        """正規化向量"""
        magnitude = self._vector_magnitude(v)
        if magnitude == 0:
            return (0, 0, 0)
        return (v[0] / magnitude, v[1] / magnitude, v[2] / magnitude)


# 保持原有的函數以維持向後相容性
def calculate_target_position(uav_gps: Dict[str, float], 
                             uav_yaw: float,
                             command: list) -> Dict[str, Any]:
    """
    根據無人機位置和移動指令計算目標位置
    格式: [經度偏移(公尺), 緯度偏移(公尺), 高度偏移(公尺), 角度偏移(度), 速度(m/s), 懸停時間(秒)]
    """
    try:
        # 計算緯度偏移（1度 ≈ 111公里）
        lat_offset = command[1] / 111000
        
        # 計算經度偏移（考慮緯度對經度距離的影響）
        lon_offset = command[0] / (111000 * math.cos(math.radians(uav_gps["lat"])))
        
        # 計算新位置
        new_lat = uav_gps["lat"] + lat_offset
        new_lon = uav_gps["lon"] + lon_offset
        new_alt = uav_gps["alt"] + command[2]
        new_yaw = (uav_yaw + command[3]) % 360  # 確保角度在0-360度範圍內
        
        # 構建任務指令格式
        mission = {
            "task": "process",
            "mission": [
                {
                    "waypoint": 1,
                    "lat": new_lat,
                    "lon": new_lon,
                    "alt": new_alt,
                    "ang": new_yaw,
                    "flight_speed": command[4],
                    "loiter_time": command[5]
                }
            ]
        }
        
        return mission
        
    except Exception as e:
        # 發生錯誤時返回原始位置
        return {
            "task": "process",
            "mission": [
                {
                    "waypoint": 1,
                    "lat": uav_gps["lat"],
                    "lon": uav_gps["lon"],
                    "alt": uav_gps["alt"],
                    "ang": uav_yaw,
                    "flight_speed": 0,
                    "loiter_time": 0
                }
            ]
        }

def calculate_spray_path(uav_gps: Dict[str, float], 
                         gimbal_info: Dict[str, Any],
                         waypoint_num: int,
                         flight_speed: float = 1.0,
                         loiter_time: float = 0.0) -> Dict[str, Any]:
    """
    根據無人機位置、雲台資訊和路徑點數量計算噴灑路徑
    返回格式:
    {
        "task": "process",
        "mission": [
            { "waypoint": 1, "lat": ..., "lon": ..., "alt": ..., "ang": ..., "flight_speed": ..., "loiter_time": ... },
            ...
        ]
    }
    """
    try:
        # 提取雲台資訊
        yaw = gimbal_info.get("yaw", 0)
        pitch = gimbal_info.get("pitch", 0)
        distance = gimbal_info.get("range_finding", {}).get("distance", 0)
        
        # 將角度轉換為弧度
        yaw_rad = math.radians(yaw)
        pitch_rad = math.radians(pitch)
        
        # 計算水平距離（考慮俯仰角）
        horizontal_distance = distance * math.cos(pitch_rad)
        
        # 計算目標位置
        target_lat = uav_gps["lat"] + (horizontal_distance * math.cos(yaw_rad)) / 111000
        target_lon = uav_gps["lon"] + (horizontal_distance * math.sin(yaw_rad)) / (111000 * math.cos(math.radians(uav_gps["lat"])))
        target_alt = uav_gps["alt"] - distance * math.sin(pitch_rad)
        
        # 獲取起始位置
        start_lat = uav_gps["lat"]
        start_lon = uav_gps["lon"]
        start_alt = uav_gps["alt"]
        
        # 計算總偏移量
        lat_offset = target_lat - start_lat
        lon_offset = target_lon - start_lon
        alt_offset = target_alt - start_alt
        
        # 生成路徑點
        mission = []
        for i in range(waypoint_num):
            ratio = (i + 1) / waypoint_num
            point = {
                "waypoint": i + 1,
                "lat": start_lat + lat_offset * ratio,
                "lon": start_lon + lon_offset * ratio,
                "alt": start_alt + alt_offset * ratio,
                "ang": yaw,
                "flight_speed": flight_speed,
                "loiter_time": loiter_time
            }
            mission.append(point)
        
        return {
            "task": "process",
            "mission": mission
        }
        
    except Exception as e:
        # 發生錯誤時返回空任務
        return {
            "task": "process",
            "mission": []
        }

def calculate_required_angle(fire_x, kim_x, image_width, fov):
    """計算需要調整的角度"""
    # 計算目標在圖像中的偏移量 (像素)
    pixel_offset = fire_x - kim_x
    
    # 計算角度偏移 (度)
    angle_per_pixel = fov / image_width
    return pixel_offset * angle_per_pixel

def calculate_required_distance(fire_y, kim_y, image_height, altitude, fov):
    """計算需要移動的距離"""
    # 計算目標在圖像中的垂直偏移 (像素)
    pixel_offset = fire_y - kim_y
    
    # 計算角度偏移 (度)
    angle_per_pixel = fov / image_height
    angle_offset = pixel_offset * angle_per_pixel
    
    # 計算需要移動的距離 (米)
    return altitude * math.tan(math.radians(angle_offset))

def calculate_intersection(uav_gps, gimbal_angles):
    """計算目標交點"""
    try:
        yaw_rad = math.radians(gimbal_angles["yaw"])
        pitch_rad = math.radians(gimbal_angles["pitch"])
        
        dx = math.cos(yaw_rad) * math.cos(pitch_rad)
        dy = math.sin(yaw_rad) * math.cos(pitch_rad)
        dz = math.sin(pitch_rad)
        
        if dz == 0:
            return None
            
        t = -uav_gps["alt"] / dz
        lat_offset = (dx * t) / 111000
        lon_offset = (dy * t) / (111000 * math.cos(math.radians(uav_gps["lat"])))
        
        return {
            "distance": math.sqrt(lat_offset**2 + lon_offset**2) * 111000,
            "height": uav_gps["alt"],
            "lat": uav_gps["lat"] + lat_offset,
            "lon": uav_gps["lon"] + lon_offset
        }
    except:
        return None

def is_in_kim_range(fire_x, fire_y, kim_config):
    """檢查目標是否在KIM噴灑範圍內"""
    kim_x = kim_config.get("x", 960)
    kim_y = kim_config.get("y", 540)
    width = kim_config.get("width", 30)
    height = kim_config.get("height", 30)
    
    # 計算矩形邊界
    x_min = kim_x - width/2
    x_max = kim_x + width/2
    y_min = kim_y - height/2
    y_max = kim_y + height/2
    
    return (x_min <= fire_x <= x_max) and (y_min <= fire_y <= y_max)

def waypoint_format(uav_gps: Dict[str, float], 
                             uav_yaw: float,
                             command: list) -> Dict[str, Any]:
    """
    產生waypoint格式的任務指令格式
    """
    try:        
        # 計算新位置
        new_lat = uav_gps["lat"]
        new_lon = uav_gps["lon"]
        new_alt = uav_gps["alt"]
        new_yaw = (uav_yaw) % 360  # 確保角度在0-360度範圍內
        new_flight_speed=command[4]
        new_loiter_time = command[5]
		
        # 構建任務指令格式
        mission = {
            "task": "process",
            "mission": [
                {
                    "waypoint": 1,
                    "lat": new_lat,
                    "lon": new_lon,
                    "alt": new_alt,
                    "ang": uav_yaw,
                    "flight_speed": new_flight_speed,
                    "loiter_time": new_loiter_time
                }
            ]
        }
        
        return mission
        
    except Exception as e:
        # 發生錯誤時返回原始位置
        return {
            "task": "process",
            "mission": [
                {
                    "waypoint": 1,
                    "lat": uav_gps["lat"],
                    "lon": uav_gps["lon"],
                    "alt": uav_gps["alt"],
                    "ang": uav_yaw,
                    "flight_speed": 0,
                    "loiter_time": 0
                }
            ]
        }