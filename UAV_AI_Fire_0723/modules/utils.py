import json
import numpy as np

def cal_next_position(LLA_Ref, NED):
    """
    將以參考點 LLA_Ref 為基準的 NED 位移向量轉換為對應的 LLA 座標。

    Parameters:
    - LLA_Ref: List or array [lat, lon, alt]，經緯度（度）與高度（公尺）
    - NED: List or array [North, East, Down]，位移向量（公尺）

    Returns:
    - lla: numpy array [lat, lon, alt]，新位置的經緯高
    """

    def rms(values):
        return np.sqrt(np.mean(np.square(values)))

    # Define referenced LLA
    lat = np.radians(LLA_Ref[0])
    lon = np.radians(LLA_Ref[1])
    alt = LLA_Ref[2]

    # Earth Parameters Definition
    a = 6378137.0  # 長半徑
    f = 1 / 298.257223563  # 扁率
    b = a * np.sqrt(1 - f * (2 - f))  # 短半徑

    # Eccentricity
    e = np.sqrt((a ** 2 - b ** 2)) / a

    # Radius of curvature
    N_val = a / np.sqrt(1 - (np.sin(lat) * e) ** 2)

    # ECEF reference point
    ECEF_Ref = np.zeros(3)
    ECEF_Ref[0] = (N_val + alt) * np.cos(lat) * np.cos(lon)
    ECEF_Ref[1] = (N_val + alt) * np.cos(lat) * np.sin(lon)
    ECEF_Ref[2] = (N_val * (1 - e ** 2) + alt) * np.sin(lat)

    # Rotation matrix from NED to ECEF
    R = np.zeros((3, 3))
    R[0, 0] = -np.cos(lon) * np.sin(lat)
    R[0, 1] = -np.sin(lon)
    R[0, 2] = -np.cos(lon) * np.cos(lat)

    R[1, 0] = -np.sin(lon) * np.sin(lat)
    R[1, 1] = np.cos(lon)
    R[1, 2] = -np.sin(lon) * np.cos(lat)

    R[2, 0] = np.cos(lat)
    R[2, 1] = 0
    R[2, 2] = -np.sin(lat)

    # Convert NED to ECEF
    NED = np.array(NED)
    ECEF = ECEF_Ref + R @ NED.T

    # Iteration parameters
    tolerance = 1e-15
    delta = np.inf
    max_iter = 150

    x, y, z = ECEF
    lla = np.zeros(3)

    # Longitude
    lla[1] = np.degrees(np.arctan2(y, x))

    # Initialize latitude and altitude
    lat_ini = 0.0
    h_ini = 0.0
    cnt = 0

    # Iterative solve for latitude and altitude
    while delta > tolerance and cnt < max_iter:
        N_temp = a / np.sqrt(1 - (np.sin(lat_ini) * e) ** 2)
        h = (np.linalg.norm([x, y]) / np.cos(lat_ini)) - N_temp
        lat = np.arctan(z / (np.linalg.norm([x, y]) * (1 - (N_temp * e ** 2 / (N_temp + h)))))

        delta = rms([lat - lat_ini, h - h_ini])

        lat_ini = lat
        h_ini = h
        cnt += 1

    # Final LLA result
    lla[0] = np.degrees(lat)
    lla[2] = h

    return lla

def cal_yaw_angle(uav_yaw, gimbal_yaw):
    """
    計算無人機與雲台之間的相對偏航角（Yaw Command），
    並確保結果在 [-180°, 180°] 範圍內，避免不必要的大角度旋轉。

    Parameters:
    - uav_yaw (float): 無人機本體的偏航角，角度制，範圍假設為 [0, 360)
    - gimbal_yaw (float): 雲台的絕對偏航角，角度制，範圍假設為 [0, 360)

    Returns:
    - yaw_command (float): 相對偏航角（目標角度 - 當前角度），值在 [-180, 180]
                           可用於控制指令輸出，表示旋轉方向與角度大小
    """

    # 同步yaw角度單位 0~360
    
    # 計算yaw command
    yaw_command = gimbal_yaw - uav_yaw

    if yaw_command > 180:
        yaw_command = yaw_command - 360
    else:
        yaw_command = yaw_command

    return yaw_command

def get_gimbal_angles(gimbal_yaw, gimbal_pitch, gimbal_roll):
    return {
        "G1_yaw": gimbal_yaw, "G1_pitch": 0, "G1_roll": 0,
        "G2_yaw": 0, "G2_pitch": gimbal_pitch, "G2_roll": 0,
        "G3_yaw": 0, "G3_pitch": 0, "G3_roll": gimbal_roll
    }

def calculate_laser_vector(UAV_x, UAV_y, UAV_z, UAV_yaw, UAV_pitch, UAV_roll,
                        G1_yaw, G1_pitch, G1_roll,
                        G2_yaw, G2_pitch, G2_roll,
                        G3_yaw, G3_pitch, G3_roll, Laser_Scalar):
    """
    Convert UAV and Gimbal poses to Cartesian coordinates for laser point calculation.
    Input:
        UAV_x (float): UAV position in the x-axis.
        UAV_y (float): UAV position in the y-axis.
        UAV_z (float): UAV position in the z-axis.
        UAV_yaw (float): UAV yaw angle in degrees.
        UAV_pitch (float): UAV pitch angle in degrees.
        UAV_roll (float): UAV roll angle in degrees.
        G1_yaw (float): Gimbal 1 yaw angle in degrees.
        G1_pitch (float): Gimbal 1 pitch angle in degrees.
        G1_roll (float): Gimbal 1 roll angle in degrees.
        G2_yaw (float): Gimbal 2 yaw angle in degrees.
        G2_pitch (float): Gimbal 2 pitch angle in degrees.
        G2_roll (float): Gimbal 2 roll angle in degrees.
        G3_yaw (float): Gimbal 3 yaw angle in degrees.
        G3_pitch (float): Gimbal 3 pitch angle in degrees.
        G3_roll (float): Gimbal 3 roll angle in degrees.
    Returns:
        T_Laser_Point (np.ndarray): The end-effector point position in Cartesian coordinates.   
    """
    # ---------------------------------
    # Load configuration from JSON file
    # ---------------------------------
    config_path = 'gimbal_config.json'

    with open(config_path, 'r') as f:
        config = json.load(f)

    Arrow_Scalar = config['Arrow_Scalar']
    t_Arrow_0 = config['t_Arrow_0']
    t_Arrow_1 = config['t_Arrow_1']
    t_Arrow_2 = config['t_Arrow_2']
    t_Arrow_3 = config['t_Arrow_3']
    t_Laser = config['t_Laser']


    def rotx(deg):
        rad = np.radians(deg)
        return np.array([
            [1, 0, 0],
            [0, np.cos(rad), -np.sin(rad)],
            [0, np.sin(rad),  np.cos(rad)]
        ])

    def roty(deg):
        rad = np.radians(deg)
        return np.array([
            [np.cos(rad), 0, np.sin(rad)],
            [0, 1, 0],
            [-np.sin(rad), 0, np.cos(rad)]
        ])

    def rotz(deg):
        rad = np.radians(deg)
        return np.array([
            [np.cos(rad), -np.sin(rad), 0],
            [np.sin(rad),  np.cos(rad), 0],
            [0, 0, 1]
        ])

    # === Generate UAV Axis-0
    R_Arrow_0_ini = np.eye(3)
    t_Arrow_0_ini = Arrow_Scalar * np.array(t_Arrow_0).reshape(3, 1)

    # === Generate Gimbal Axis-1
    R_Arrow_1_ini = np.eye(3)
    t_Arrow_1_ini = Arrow_Scalar * np.array(t_Arrow_1).reshape(3, 1)

    # === Generate Gimbal Axis-2
    R_Arrow_2_ini = np.eye(3)
    t_Arrow_2_ini = Arrow_Scalar * np.array(t_Arrow_2).reshape(3, 1)

    # === Generate Gimbal Axis-3
    R_Arrow_3_ini = np.eye(3)
    t_Arrow_3_ini = Arrow_Scalar * np.array(t_Arrow_3).reshape(3, 1)

    # === Generate Laser Axis
    # Laser_Scalar = 133
    R_Laser_ini = np.eye(3)
    t_Laser_ini = np.array(t_Laser).reshape(3, 1)

    # Update UAV Axis-0
    R_Arrow_0 = rotz(UAV_yaw) @ roty(UAV_pitch) @ rotx(UAV_roll)
    t_Arrow_0 = np.array([UAV_x, UAV_y, UAV_z]).reshape(3, 1) + t_Arrow_0_ini

    # Update Gimbal Axes
    R_Arrow_1 = rotz(G1_yaw) @ roty(G1_pitch) @ rotx(G1_roll)
    t_Arrow_1 = t_Arrow_1_ini

    R_Arrow_2 = rotz(G2_yaw) @ roty(G2_pitch) @ rotx(G2_roll)
    t_Arrow_2 = t_Arrow_2_ini

    R_Arrow_3 = rotz(G3_yaw) @ roty(G3_pitch) @ rotx(G3_roll)
    t_Arrow_3 = t_Arrow_3_ini

    # Laser
    R_Laser = np.eye(3)
    t_Laser = t_Laser_ini

    # Coordinate Transformation Matrices
    def make_transform(R, t):
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = t.flatten()
        return T

    T_UAV_to_Global = make_transform(R_Arrow_0, t_Arrow_0)
    T_G1_to_UAV     = make_transform(R_Arrow_1, t_Arrow_1)
    T_G2_to_G1      = make_transform(R_Arrow_2, t_Arrow_2)
    T_G3_to_G2      = make_transform(R_Arrow_3, t_Arrow_3)
    T_Laser_to_G3   = make_transform(R_Laser, t_Laser)

    Laser_Depth = np.array([Laser_Scalar, 0, 0, 1]).reshape(4, 1)

    # End-effector Point Position
    T_Laser_Point = T_UAV_to_Global @ T_G1_to_UAV @ T_G2_to_G1 @ T_G3_to_G2 @ T_Laser_to_G3 @ Laser_Depth
    T_Laser_vector = T_Laser_Point[:3].flatten()
    # print("Laser Position in NED:", T_Laser_vector)
    return T_Laser_vector

def find_laser_intersection(P1, V1, P2, V2):
    """
    P1, P2: 起點 (np.array of shape (3,))
    V1, V2: 方向向量 (np.array of shape (3,))
    回傳：兩向量最近點的中點
    """
    V1 = V1 / np.linalg.norm(V1)
    V2 = V2 / np.linalg.norm(V2)

    w0 = P1 - P2
    a = np.dot(V1, V1)
    b = np.dot(V1, V2)
    c = np.dot(V2, V2)
    d = np.dot(V1, w0)
    e = np.dot(V2, w0)

    denominator = a * c - b ** 2
    if abs(denominator) < 1e-6:
        # print("⚠️ 向量幾乎平行，無法計算精確交點")
        return None

    sc = (b * e - c * d) / denominator
    tc = (a * e - b * d) / denominator

    closest_point_on_L1 = P1 + sc * V1
    closest_point_on_L2 = P2 + tc * V2

    midpoint = (closest_point_on_L1 + closest_point_on_L2) / 2.0
    return midpoint

def cal_target_location(LLA_1, gimbal_pitch_1, gimbal_yaw_1,gimbal_roll_1,
                        UAV_x1, UAV_y1, UAV_z1, UAV_yaw_1, UAV_pitch_1, UAV_roll_1,
                        LLA_2, gimbal_pitch_2, gimbal_yaw_2,gimbal_roll_2,
                        UAV_x2, UAV_y2, UAV_z2, UAV_yaw_2, UAV_pitch_2, UAV_roll_2):
    '''
    UAV_x1, UAV_y1, UAV_z1, UAV_x2, UAV_y2, UAV_z2 : in NED frame
    '''

    angles_1 = get_gimbal_angles(gimbal_pitch_1, gimbal_yaw_1, gimbal_roll_1)
    angles_2 = get_gimbal_angles(gimbal_pitch_2, gimbal_yaw_2, gimbal_roll_2)

    T_Laser_vector_1 = calculate_laser_vector(UAV_x1, UAV_y1, UAV_z1, UAV_yaw_1, UAV_pitch_1, UAV_roll_1,
            Laser_Scalar=1.0, **angles_1)
    
    T_Laser_vector_2 = calculate_laser_vector(UAV_x2, UAV_y2, UAV_z2, UAV_yaw_2, UAV_pitch_2, UAV_roll_2,
            Laser_Scalar=1.0, **angles_2)
    
    P1 = np.array([UAV_x1, UAV_y1, UAV_z1])
    P2 = np.array([UAV_x2, UAV_y2, UAV_z2])

    target_point = find_laser_intersection(P1, T_Laser_vector_1, P2, T_Laser_vector_2)

    if target_point is None:
        # print("⚠️ 無法計算交點")
        return None, None, None, None

    distance = np.sqrt(
    (UAV_x1 - target_point[0]) ** 2 +
    (UAV_y1 - target_point[1]) ** 2 +
    (UAV_z1 - target_point[2]) ** 2)

    height = abs(UAV_z1 - target_point[2])

    vector_ned = target_point - P1

    target_lla = cal_next_position(LLA_1, vector_ned)

    return target_lla, vector_ned, distance, height

def generate_waypoints(target_lla, vector_ned, distance, waypoint_num):
    waypoint_list = []
    delta_d = distance / waypoint_num
    # delta_h = height // waypoint_num

    vector_unit = vector_ned / np.linalg.norm(vector_ned)

    for i in range(1, waypoint_num + 1):
        # 每個 waypoint 向量是 -vector_unit * 距離 + 垂直方向調整高度
        step_vector = -vector_unit * delta_d * i
        waypoint = cal_next_position(target_lla, step_vector)
        waypoint_list.append(waypoint)

    return waypoint_list

