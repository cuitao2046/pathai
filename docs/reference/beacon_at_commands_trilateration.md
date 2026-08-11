# RF-B-SR1 信标 AT 配置命令清单（三点定位全楼层部署 · 324 个）

> 生成依据：`result\beacon_deployment_plan_trilateration.json`（schemaVersion 1.1-constructable）  
> 目标 UUID：`B9407F30-F5F8-466E-AFF9-25556B57FE6D`  
> 发射功率统一 **-10 dBm**（设备档位无 -8/-12，详见 docs/08 §1.3）  
> 广播间隔：沿用部署计划 `broadcastInterval`（当前全部 300 ms）  
> ⚠️ **RSSI@1m 为占位值 -59 dBm（`C5`）**，须现场实测后逐信标替换（校准方法见文末）

## 使用方式与注意事项

1. 手机装 **nRF Connect**，搜到信标（默认名 `RFstar_XXXX`）并连接。
2. 在 **RX 特征值**（写入通道，UUID `6E400002…`）发送以下命令；**AT 指令须大写、不带回车换行**。
3. 主机端 MTU 须 **≥128 字节**，否则指令无法设置。
4. 所有设置指令**立即生效且掉电保存**，无需 RESET/SAVE。
5. ⚠️ `AT+ADS` 的**模式必须保持 1（可连接）**；一旦设 0 不可连接，将永久无法再改任何参数。
6. 建议先 `AT+NAME=BK-xx-xxx` 命名，便于在 nRF Connect 中区分各信标。

## 一、逐信标 AT 命令

### BK-01-001  ·  F1 · stair
- 位置：1F 楼梯入口（II-B1-01#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-36.252, -75.998)　吸附偏移：0.01m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-001
AT+BEACON=004C,0001,2775,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-002  ·  F1 · stair
- 位置：1F 楼梯入口（II-B1-02#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-13.793, -75.947)　吸附偏移：0.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-002
AT+BEACON=004C,0001,2776,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-003  ·  F1 · stair
- 位置：1F 楼梯入口（II-B1-03#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-41.502, -65.124)　吸附偏移：2.03m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-003
AT+BEACON=004C,0001,2777,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-004  ·  F1 · stair
- 位置：1F 楼梯入口（II-B2-01#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-78.035, -17.165)　吸附偏移：0.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-004
AT+BEACON=004C,0001,2778,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-005  ·  F1 · stair
- 位置：1F 楼梯入口（II-B2-02#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-55.779, -17.165)　吸附偏移：0.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-005
AT+BEACON=004C,0001,2779,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-006  ·  F1 · stair
- 位置：1F 楼梯入口（II-B3-01#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-46.003, 6.945)　吸附偏移：0.25m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-006
AT+BEACON=004C,0001,277A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-007  ·  F1 · stair
- 位置：1F 楼梯入口（II-B3-02#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-21.621, 7.928)　吸附偏移：0.17m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-007
AT+BEACON=004C,0001,277B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-008  ·  F1 · stair
- 位置：1F 楼梯入口（II-B3-03#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-60.394, 15.597)　吸附偏移：0.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-008
AT+BEACON=004C,0001,277C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-009  ·  F1 · stair
- 位置：1F 楼梯入口（II-B4-01#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-52.776, -33.936)　吸附偏移：0.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-009
AT+BEACON=004C,0001,277D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-010  ·  F1 · stair
- 位置：1F 楼梯入口（II-B4-02#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-18.211, -51.127)　吸附偏移：0.9m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-010
AT+BEACON=004C,0001,277E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-011  ·  F1 · stair
- 位置：1F 楼梯预警（II-B1-01#ST 前约 4m）
- 坐标(pt)：(-33.584, -78.228)　吸附偏移：0.02m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-011
AT+BEACON=004C,0001,277F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-012  ·  F1 · stair
- 位置：1F 楼梯预警（II-B1-02#ST 前约 4m）
- 坐标(pt)：(-14.212, -71.72)　吸附偏移：0.9m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-012
AT+BEACON=004C,0001,2780,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-013  ·  F1 · stair
- 位置：1F 楼梯预警（II-B1-03#ST 前约 4m）
- 坐标(pt)：(-38.473, -69.035)　吸附偏移：0.62m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-013
AT+BEACON=004C,0001,2781,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-014  ·  F1 · stair
- 位置：1F 楼梯预警（II-B2-01#ST 前约 4m）
- 坐标(pt)：(-80.274, -19.445)　吸附偏移：0.34m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-014
AT+BEACON=004C,0001,2782,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-015  ·  F1 · stair
- 位置：1F 楼梯预警（II-B2-02#ST 前约 4m）
- 坐标(pt)：(-59.061, -18.096)　吸附偏移：0.1m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-015
AT+BEACON=004C,0001,2783,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-016  ·  F1 · stair
- 位置：1F 楼梯预警（II-B3-01#ST 前约 4m）
- 坐标(pt)：(-50.097, 8.094)　吸附偏移：0.73m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-016
AT+BEACON=004C,0001,2784,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-017  ·  F1 · stair
- 位置：1F 楼梯预警（II-B3-02#ST 前约 4m）
- 坐标(pt)：(-20.022, 5.744)　吸附偏移：0.66m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-017
AT+BEACON=004C,0001,2785,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-018  ·  F1 · stair
- 位置：1F 楼梯预警（II-B3-03#ST 前约 4m）
- 坐标(pt)：(-60.445, 11.801)　吸附偏移：0.35m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-018
AT+BEACON=004C,0001,2786,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-019  ·  F1 · stair
- 位置：1F 楼梯预警（II-B4-01#ST 前约 4m）
- 坐标(pt)：(-51.067, -36.133)　吸附偏移：0.91m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-019
AT+BEACON=004C,0001,2787,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-020  ·  F1 · stair
- 位置：1F 楼梯预警（II-B4-02#ST 前约 4m）
- 坐标(pt)：(-17.735, -53.577)　吸附偏移：0.78m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-020
AT+BEACON=004C,0001,2788,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-021  ·  F1 · elevator
- 位置：1F 电梯口（II-01#EL）· 门套或呼梯板旁
- 坐标(pt)：(-28.955, -74.887)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-021
AT+BEACON=004C,0001,2789,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-022  ·  F1 · elevator
- 位置：1F 电梯口（II-02#EL）· 门套或呼梯板旁
- 坐标(pt)：(-62.946, -16.105)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-022
AT+BEACON=004C,0001,278A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-023  ·  F1 · elevator
- 位置：1F 电梯口（II-03#EL）· 门套或呼梯板旁
- 坐标(pt)：(-37.351, 9.084)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-023
AT+BEACON=004C,0001,278B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-024  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-30.608, -84.522)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-024
AT+BEACON=004C,0001,278C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-025  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-28.38, -83.987)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-025
AT+BEACON=004C,0001,278D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-026  ·  F1 · door
- 位置：1F 门口（门洞）
- 坐标(pt)：(-14.632, -66.873)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-026
AT+BEACON=004C,0001,278E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-027  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-16.405, -64.547)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-027
AT+BEACON=004C,0001,278F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-028  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-16.405, -57.798)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-028
AT+BEACON=004C,0001,2790,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-029  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-58.391, 0.978)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-029
AT+BEACON=004C,0001,2791,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-030  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-58.391, -9.764)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-030
AT+BEACON=004C,0001,2792,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-031  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-40.925, 1.958)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-031
AT+BEACON=004C,0001,2793,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-032  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-34.176, 1.958)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-032
AT+BEACON=004C,0001,2794,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-033  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-25.778, 1.958)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-033
AT+BEACON=004C,0001,2795,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-034  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-76.162, -23.237)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-034
AT+BEACON=004C,0001,2796,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-035  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-67.763, -23.237)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-035
AT+BEACON=004C,0001,2797,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-036  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-41.899, 19.424)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-036
AT+BEACON=004C,0001,2798,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-037  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-41.899, 26.072)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-037
AT+BEACON=004C,0001,2799,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-038  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-41.899, 11.026)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-038
AT+BEACON=004C,0001,279A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-039  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-24.804, 19.424)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-039
AT+BEACON=004C,0001,279B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-040  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-24.804, 26.072)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-040
AT+BEACON=004C,0001,279C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-041  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-75.486, 0.978)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-041
AT+BEACON=004C,0001,279D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-042  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-75.486, -9.764)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-042
AT+BEACON=004C,0001,279E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-043  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-32.937, -83.88)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-043
AT+BEACON=004C,0001,279F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-044  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-7.237, -38.9)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-044
AT+BEACON=004C,0001,27A0,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-045  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-7.237, -36.667)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-045
AT+BEACON=004C,0001,27A1,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-046  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-44.219, -57.752)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-046
AT+BEACON=004C,0001,27A2,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-047  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-47.679, -57.752)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-047
AT+BEACON=004C,0001,27A3,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-048  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-12.872, -89.435)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-048
AT+BEACON=004C,0001,27A4,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-049  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-10.645, -89.435)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-049
AT+BEACON=004C,0001,27A5,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-050  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-8.109, -58.802)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-050
AT+BEACON=004C,0001,27A6,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-051  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-59.38, -33.984)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-051
AT+BEACON=004C,0001,27A7,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-052  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-16.061, 8.075)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-052
AT+BEACON=004C,0001,27A8,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-053  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-61.578, -29.539)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-053
AT+BEACON=004C,0001,27A9,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-054  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-59.203, -29.539)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-054
AT+BEACON=004C,0001,27AA,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-055  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-42.364, -80.511)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-055
AT+BEACON=004C,0001,27AB,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-056  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-83.783, -16.46)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-056
AT+BEACON=004C,0001,27AC,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-057  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-26.807, -14.642)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-057
AT+BEACON=004C,0001,27AD,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-058  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-59.465, -49.858)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-058
AT+BEACON=004C,0001,27AE,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-059  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-59.465, -52.091)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-059
AT+BEACON=004C,0001,27AF,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-060  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-25.632, -83.987)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-060
AT+BEACON=004C,0001,27B0,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-061  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-21.526, -83.987)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-061
AT+BEACON=004C,0001,27B1,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-062  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-18.777, -83.987)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-062
AT+BEACON=004C,0001,27B2,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-063  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-73.918, -54.554)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-063
AT+BEACON=004C,0001,27B3,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-064  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-50.432, -39.884)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-064
AT+BEACON=004C,0001,27B4,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-065  ·  F1 · door
- 位置：1F 门口（门洞）
- 坐标(pt)：(-23.031, 17.098)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-065
AT+BEACON=004C,0001,27B5,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-066  ·  F1 · door
- 位置：1F 门口（普通门）
- 坐标(pt)：(-23.678, -63.02)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-066
AT+BEACON=004C,0001,27B6,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-067  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-62.459, -21.728)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-067
AT+BEACON=004C,0001,27B7,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-068  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-50.578, -21.728)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-068
AT+BEACON=004C,0001,27B8,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-069  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-16.38, -60.704)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-069
AT+BEACON=004C,0001,27B9,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-070  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-42.338, -27.391)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-070
AT+BEACON=004C,0001,27BA,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-071  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-80.565, -22.412)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-071
AT+BEACON=004C,0001,27BB,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-072  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-70.109, -22.412)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-072
AT+BEACON=004C,0001,27BC,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-073  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-24.778, 23.268)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-073
AT+BEACON=004C,0001,27BD,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-074  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-24.218, -15.3)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-074
AT+BEACON=004C,0001,27BE,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-075  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-24.218, -17.576)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-075
AT+BEACON=004C,0001,27BF,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-076  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-28.029, -28.635)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-076
AT+BEACON=004C,0001,27C0,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-077  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-20.022, 3.467)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-077
AT+BEACON=004C,0001,27C1,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-078  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-33.584, -80.504)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-078
AT+BEACON=004C,0001,27C2,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-079  ·  F1 · door
- 位置：1F 门口（防火门）
- 坐标(pt)：(-26.228, -63.02)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-079
AT+BEACON=004C,0001,27C3,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-080  ·  F1 · door
- 位置：1F 门口（门洞）
- 坐标(pt)：(-64.545, -31.835)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-080
AT+BEACON=004C,0001,27C4,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-081  ·  F1 · door
- 位置：1F 门口（门洞）
- 坐标(pt)：(-52.148, -19.19)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-081
AT+BEACON=004C,0001,27C5,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-082  ·  F1 · intersection
- 位置：1F 交叉口（交叉口10）
- 坐标(pt)：(-16.91, -69.123)　吸附偏移：1.2m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-082
AT+BEACON=004C,0001,27C6,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-083  ·  F1 · intersection
- 位置：1F 交叉口（交叉口11）
- 坐标(pt)：(-17.012, -78.169)　吸附偏移：1.59m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-083
AT+BEACON=004C,0001,27C7,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-084  ·  F1 · intersection
- 位置：1F 交叉口（交叉口13）
- 坐标(pt)：(-67.095, 1.555)　吸附偏移：7.6m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-084
AT+BEACON=004C,0001,27C8,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-085  ·  F1 · intersection
- 位置：1F 交叉口（交叉口18）
- 坐标(pt)：(-41.699, -78.169)　吸附偏移：0.98m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-085
AT+BEACON=004C,0001,27C9,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-086  ·  F1 · intersection
- 位置：1F 交叉口（交叉口20）
- 坐标(pt)：(-25.41, 12.347)　吸附偏移：1.7m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-086
AT+BEACON=004C,0001,27CA,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-087  ·  F1 · intersection
- 位置：1F 交叉口（交叉口22）
- 坐标(pt)：(-27.403, 26.947)　吸附偏移：1.38m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-087
AT+BEACON=004C,0001,27CB,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-088  ·  F1 · intersection
- 位置：1F 交叉口（交叉口24）
- 坐标(pt)：(-33.605, 26.744)　吸附偏移：1.09m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-088
AT+BEACON=004C,0001,27CC,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-089  ·  F1 · intersection
- 位置：1F 交叉口（交叉口25）
- 坐标(pt)：(-33.605, 10.449)　吸附偏移：1.59m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-089
AT+BEACON=004C,0001,27CD,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-090  ·  F1 · intersection
- 位置：1F 交叉口（交叉口27）
- 坐标(pt)：(-16.612, 3.403)　吸附偏移：1.17m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-090
AT+BEACON=004C,0001,27CE,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-091  ·  F1 · intersection
- 位置：1F 交叉口（交叉口28）
- 坐标(pt)：(-33.605, 5.802)　吸附偏移：1.43m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-091
AT+BEACON=004C,0001,27CF,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-092  ·  F1 · intersection
- 位置：1F 交叉口（交叉口31）
- 坐标(pt)：(-42.004, -32.038)　吸附偏移：4.64m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-092
AT+BEACON=004C,0001,27D0,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-093  ·  F1 · intersection
- 位置：1F 交叉口（交叉口33）
- 坐标(pt)：(-42.004, -13.82)　吸附偏移：1.3m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-093
AT+BEACON=004C,0001,27D1,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-094  ·  F1 · intersection
- 位置：1F 交叉口（交叉口34）
- 坐标(pt)：(-74.893, -12.791)　吸附偏移：1.54m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-094
AT+BEACON=004C,0001,27D2,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-095  ·  F1 · intersection
- 位置：1F 交叉口（交叉口35）
- 坐标(pt)：(-73.991, -21.786)　吸附偏移：1.52m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-095
AT+BEACON=004C,0001,27D3,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-096  ·  F1 · intersection
- 位置：1F 交叉口（交叉口36）
- 坐标(pt)：(-61.663, -32.038)　吸附偏移：1.18m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-096
AT+BEACON=004C,0001,27D4,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-097  ·  F1 · intersection
- 位置：1F 交叉口（交叉口37）
- 坐标(pt)：(-42.004, -20.461)　吸附偏移：2.25m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-097
AT+BEACON=004C,0001,27D5,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-098  ·  F1 · intersection
- 位置：1F 交叉口（交叉口39）
- 坐标(pt)：(-75.089, -6.342)　吸附偏移：1.85m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-098
AT+BEACON=004C,0001,27D6,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-099  ·  F1 · intersection
- 位置：1F 交叉口（交叉口40）
- 坐标(pt)：(-58.794, -6.342)　吸附偏移：1.9m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-099
AT+BEACON=004C,0001,27D7,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-100  ·  F1 · intersection
- 位置：1F 交叉口（交叉口42）
- 坐标(pt)：(-66.691, -14.74)　吸附偏移：1.9m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-100
AT+BEACON=004C,0001,27D8,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-101  ·  F1 · intersection
- 位置：1F 交叉口（交叉口43）
- 坐标(pt)：(-66.691, -19.393)　吸附偏移：0.97m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-101
AT+BEACON=004C,0001,27D9,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-102  ·  F1 · intersection
- 位置：1F 交叉口（交叉口47）
- 坐标(pt)：(-34.086, -34.839)　吸附偏移：1.94m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-102
AT+BEACON=004C,0001,27DA,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-103  ·  F1 · intersection
- 位置：1F 交叉口（交叉口49）
- 坐标(pt)：(-9.464, -14.74)　吸附偏移：1.26m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-103
AT+BEACON=004C,0001,27DB,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-104  ·  F1 · intersection
- 位置：1F 交叉口（交叉口51）
- 坐标(pt)：(-28.722, -40.43)　吸附偏移：4.49m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-104
AT+BEACON=004C,0001,27DC,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-105  ·  F1 · intersection
- 位置：1F 交叉口（交叉口53）
- 坐标(pt)：(-42.006, -39.938)　吸附偏移：3.95m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-105
AT+BEACON=004C,0001,27DD,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-106  ·  F1 · intersection
- 位置：1F 交叉口（交叉口55）
- 坐标(pt)：(-42.006, -48.826)　吸附偏移：4.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-106
AT+BEACON=004C,0001,27DE,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-107  ·  F1 · intersection
- 位置：1F 交叉口（交叉口57）
- 坐标(pt)：(-33.605, -54.675)　吸附偏移：2.2m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-107
AT+BEACON=004C,0001,27DF,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-108  ·  F1 · intersection
- 位置：1F 交叉口（交叉口59）
- 坐标(pt)：(-8.213, -79.782)　吸附偏移：1.19m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-108
AT+BEACON=004C,0001,27E0,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-109  ·  F1 · intersection
- 位置：1F 交叉口（交叉口60）
- 坐标(pt)：(-10.613, -77.972)　吸附偏移：1.12m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-109
AT+BEACON=004C,0001,27E1,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-110  ·  F1 · intersection
- 位置：1F 交叉口（交叉口61）
- 坐标(pt)：(-29.019, -78.169)　吸附偏移：1.22m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-110
AT+BEACON=004C,0001,27E2,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-111  ·  F1 · intersection
- 位置：1F 交叉口（交叉口63）
- 坐标(pt)：(-26.407, -78.169)　吸附偏移：1.22m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-111
AT+BEACON=004C,0001,27E3,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-112  ·  F1 · intersection
- 位置：1F 交叉口（交叉口65）
- 坐标(pt)：(-23.855, -78.169)　吸附偏移：1.28m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-112
AT+BEACON=004C,0001,27E4,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-113  ·  F1 · intersection
- 位置：1F 交叉口（交叉口67）
- 坐标(pt)：(-20.805, -78.169)　吸附偏移：1.28m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-113
AT+BEACON=004C,0001,27E5,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-114  ·  F1 · intersection
- 位置：1F 交叉口（交叉口72）
- 坐标(pt)：(-61.797, -19.393)　吸附偏移：1.64m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-114
AT+BEACON=004C,0001,27E6,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-115  ·  F1 · intersection
- 位置：1F 交叉口（交叉口75）
- 坐标(pt)：(-83.691, -19.463)　吸附偏移：1.22m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-115
AT+BEACON=004C,0001,27E7,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-116  ·  F1 · intersection
- 位置：1F 交叉口（交叉口3 · 东向）
- 坐标(pt)：(-37.351, 3.25)　吸附偏移：1.29m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-116
AT+BEACON=004C,0001,27E8,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-117  ·  F1 · intersection
- 位置：1F 交叉口（交叉口3 · 西向）
- 坐标(pt)：(-41.299, 5.802)　吸附偏移：1.26m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-117
AT+BEACON=004C,0001,27E9,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-118  ·  F1 · intersection
- 位置：1F 交叉口（交叉口4 · 南向）
- 坐标(pt)：(-41.299, 24.015)　吸附偏移：2.83m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-118
AT+BEACON=004C,0001,27EA,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-119  ·  F1 · intersection
- 位置：1F 交叉口（交叉口4 · 东向）
- 坐标(pt)：(-38.055, 26.947)　吸附偏移：1.44m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-119
AT+BEACON=004C,0001,27EB,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-120  ·  F1 · intersection
- 位置：1F 交叉口（交叉口6 · 西向）
- 坐标(pt)：(-58.997, -1.407)　吸附偏移：2.76m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-120
AT+BEACON=004C,0001,27EC,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-121  ·  F1 · intersection
- 位置：1F 交叉口（交叉口10 · 北向）
- 坐标(pt)：(-17.012, -66.825)　吸附偏移：1.11m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-121
AT+BEACON=004C,0001,27ED,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-122  ·  F1 · intersection
- 位置：1F 交叉口（交叉口12 · 南向）
- 坐标(pt)：(-41.299, 17.248)　吸附偏移：6.62m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-122
AT+BEACON=004C,0001,27EE,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-123  ·  F1 · intersection
- 位置：1F 交叉口（交叉口19 · 北向）
- 坐标(pt)：(-32.907, -73.523)　吸附偏移：1.24m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-123
AT+BEACON=004C,0001,27EF,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-124  ·  F1 · intersection
- 位置：1F 交叉口（交叉口20 · 北向）
- 坐标(pt)：(-28.406, 10.449)　吸附偏移：2.95m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-124
AT+BEACON=004C,0001,27F0,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-125  ·  F1 · intersection
- 位置：1F 交叉口（交叉口21 · 北向）
- 坐标(pt)：(-41.299, 13.471)　吸附偏移：2.9m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-125
AT+BEACON=004C,0001,27F1,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-126  ·  F1 · intersection
- 位置：1F 交叉口（交叉口23 · 南向）
- 坐标(pt)：(-25.41, 17.248)　吸附偏移：1.63m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-126
AT+BEACON=004C,0001,27F2,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-127  ·  F1 · intersection
- 位置：1F 交叉口（交叉口25 · 东向）
- 坐标(pt)：(-31.053, 10.252)　吸附偏移：1.8m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-127
AT+BEACON=004C,0001,27F3,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-128  ·  F1 · intersection
- 位置：1F 交叉口（交叉口26 · 北向）
- 坐标(pt)：(-28.203, 6.304)　吸附偏移：1.19m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-128
AT+BEACON=004C,0001,27F4,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-129  ·  F1 · intersection
- 位置：1F 交叉口（交叉口26 · 西向）
- 坐标(pt)：(-29.358, 3.25)　吸附偏移：1.28m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-129
AT+BEACON=004C,0001,27F5,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-130  ·  F1 · intersection
- 位置：1F 交叉口（交叉口28 · 东向）
- 坐标(pt)：(-32.006, 3.403)　吸附偏移：1.16m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-130
AT+BEACON=004C,0001,27F6,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-131  ·  F1 · intersection
- 位置：1F 交叉口（交叉口29 · 东向）
- 坐标(pt)：(-23.556, 3.403)　吸附偏移：1.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-131
AT+BEACON=004C,0001,27F7,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-132  ·  F1 · intersection
- 位置：1F 交叉口（交叉口32 · 东向）
- 坐标(pt)：(-58.302, -14.737)　吸附偏移：2.1m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-132
AT+BEACON=004C,0001,27F8,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-133  ·  F1 · intersection
- 位置：1F 交叉口（交叉口34 · 南向）
- 坐标(pt)：(-72.093, -14.74)　吸附偏移：1.28m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-133
AT+BEACON=004C,0001,27F9,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-134  ·  F1 · intersection
- 位置：1F 交叉口（交叉口35 · 北向）
- 坐标(pt)：(-72.093, -18.892)　吸附偏移：1.36m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-134
AT+BEACON=004C,0001,27FA,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-135  ·  F1 · intersection
- 位置：1F 交叉口（交叉口37 · 西向）
- 坐标(pt)：(-49.901, -19.393)　吸附偏移：3.76m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-135
AT+BEACON=004C,0001,27FB,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-136  ·  F1 · intersection
- 位置：1F 交叉口（交叉口38 · 南向）
- 坐标(pt)：(-74.893, -1.231)　吸附偏移：3.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-136
AT+BEACON=004C,0001,27FC,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-137  ·  F1 · intersection
- 位置：1F 交叉口（交叉口38 · 东向）
- 坐标(pt)：(-71.719, 1.854)　吸附偏移：1.74m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-137
AT+BEACON=004C,0001,27FD,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-138  ·  F1 · intersection
- 位置：1F 交叉口（交叉口39 · 北向）
- 坐标(pt)：(-74.893, -4.236)　吸附偏移：1.65m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-138
AT+BEACON=004C,0001,27FE,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-139  ·  F1 · intersection
- 位置：1F 交叉口（交叉口43 · 西向）
- 坐标(pt)：(-69.243, -19.19)　吸附偏移：1.23m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-139
AT+BEACON=004C,0001,27FF,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-140  ·  F1 · intersection
- 位置：1F 交叉口（交叉口45 · 南向）
- 坐标(pt)：(-42.004, -29.436)　吸附偏移：2.8m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-140
AT+BEACON=004C,0001,2800,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-141  ·  F1 · intersection
- 位置：1F 交叉口（交叉口47 · 南向）
- 坐标(pt)：(-34.086, -37.122)　吸附偏移：1.83m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-01-141
AT+BEACON=004C,0001,2801,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-142  ·  F1 · intersection
- 位置：1F 交叉口（交叉口47 · 西向）
- 坐标(pt)：(-37.81, -32.038)　吸附偏移：3.65m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-142
AT+BEACON=004C,0001,2802,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-143  ·  F1 · intersection
- 位置：1F 交叉口（交叉口48 · 北向）
- 坐标(pt)：(-7.959, -33.758)　吸附偏移：2.5m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-143
AT+BEACON=004C,0001,2803,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-144  ·  F1 · intersection
- 位置：1F 交叉口（交叉口49 · 南向）
- 坐标(pt)：(-7.959, -17.343)　吸附偏移：2.6m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-144
AT+BEACON=004C,0001,2804,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-145  ·  F1 · intersection
- 位置：1F 交叉口（交叉口49 · 西向）
- 坐标(pt)：(-12.936, -14.74)　吸附偏移：1.01m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-145
AT+BEACON=004C,0001,2805,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-146  ·  F1 · intersection
- 位置：1F 交叉口（交叉口51 · 东向）
- 坐标(pt)：(-25.207, -32.038)　吸附偏移：4.16m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-146
AT+BEACON=004C,0001,2806,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-147  ·  F1 · intersection
- 位置：1F 交叉口（交叉口55 · 西向）
- 坐标(pt)：(-49.901, -48.829)　吸附偏移：2.92m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-147
AT+BEACON=004C,0001,2807,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-148  ·  F1 · intersection
- 位置：1F 交叉口（交叉口57 · 西向）
- 坐标(pt)：(-37.805, -57.227)　吸附偏移：2.48m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-148
AT+BEACON=004C,0001,2808,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-149  ·  F1 · intersection
- 位置：1F 交叉口（交叉口58 · 东向）
- 坐标(pt)：(-39.299, -78.169)　吸附偏移：1.25m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-149
AT+BEACON=004C,0001,2809,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-150  ·  F1 · intersection
- 位置：1F 交叉口（交叉口59 · 南向）
- 坐标(pt)：(-8.512, -81.819)　吸附偏移：1.42m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-150
AT+BEACON=004C,0001,280A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-151  ·  F1 · intersection
- 位置：1F 交叉口（交叉口60 · 北向）
- 坐标(pt)：(-8.213, -75.947)　吸附偏移：0.88m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-151
AT+BEACON=004C,0001,280B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-152  ·  F1 · intersection
- 位置：1F 交叉口（交叉口70 · 南向）
- 坐标(pt)：(-16.307, -81.921)　吸附偏移：0.98m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-152
AT+BEACON=004C,0001,280C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-153  ·  F1 · intersection
- 位置：1F 交叉口（交叉口70 · 东向）
- 坐标(pt)：(-14.409, -77.972)　吸附偏移：1.48m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-153
AT+BEACON=004C,0001,280D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-154  ·  F1 · intersection
- 位置：1F 交叉口（交叉口71 · 北向）
- 坐标(pt)：(-41.502, -10.558)　吸附偏移：2.91m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-154
AT+BEACON=004C,0001,280E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-01-155  ·  F1 · intersection
- 位置：1F 交叉口（交叉口71 · 西向）
- 坐标(pt)：(-49.247, -14.74)　吸附偏移：3.57m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-01-155
AT+BEACON=004C,0001,280F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-001  ·  F2 · stair
- 位置：2F 楼梯入口（II-B1-01#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-36.592, -75.977)　吸附偏移：0.29m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-001
AT+BEACON=004C,0002,2810,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-002  ·  F2 · stair
- 位置：2F 楼梯入口（II-B1-02#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-12.81, -75.946)　吸附偏移：0.35m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-002
AT+BEACON=004C,0002,2811,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-003  ·  F2 · stair
- 位置：2F 楼梯入口（II-B1-03#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-39.509, -64.197)　吸附偏移：0.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-003
AT+BEACON=004C,0002,2812,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-004  ·  F2 · stair
- 位置：2F 楼梯入口（II-B2-01#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-78.578, -17.195)　吸附偏移：0.29m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-004
AT+BEACON=004C,0002,2813,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-005  ·  F2 · stair
- 位置：2F 楼梯入口（II-B2-02#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-54.796, -17.163)　吸附偏移：0.35m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-005
AT+BEACON=004C,0002,2814,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-006  ·  F2 · stair
- 位置：2F 楼梯入口（II-B3-01#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-44.985, 7.996)　吸附偏移：0.29m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-006
AT+BEACON=004C,0002,2815,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-007  ·  F2 · stair
- 位置：2F 楼梯入口（II-B3-02#ST）· 装前室/梯段侧墙
- 坐标(pt)：(-21.202, 8.026)　吸附偏移：0.35m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-007
AT+BEACON=004C,0002,2816,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-008  ·  F2 · stair
- 位置：2F 楼梯预警（II-B1-01#ST 前约 4m）
- 坐标(pt)：(-38.055, -77.972)　吸附偏移：1.36m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-008
AT+BEACON=004C,0002,2817,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-009  ·  F2 · stair
- 位置：2F 楼梯预警（II-B1-02#ST 前约 4m）
- 坐标(pt)：(-15.851, -73.719)　吸附偏移：0.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-009
AT+BEACON=004C,0002,2818,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-010  ·  F2 · stair
- 位置：2F 楼梯预警（II-B1-03#ST 前约 4m）
- 坐标(pt)：(-37.643, -62.128)　吸附偏移：1.04m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-010
AT+BEACON=004C,0002,2819,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-011  ·  F2 · stair
- 位置：2F 楼梯预警（II-B2-01#ST 前约 4m）
- 坐标(pt)：(-80.041, -19.19)　吸附偏移：1.36m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-011
AT+BEACON=004C,0002,281A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-012  ·  F2 · stair
- 位置：2F 楼梯预警（II-B2-02#ST 前约 4m）
- 坐标(pt)：(-57.872, -14.919)　吸附偏移：0.21m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-012
AT+BEACON=004C,0002,281B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-013  ·  F2 · stair
- 位置：2F 楼梯预警（II-B3-01#ST 前约 4m）
- 坐标(pt)：(-42.725, 10.25)　吸附偏移：0.09m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-013
AT+BEACON=004C,0002,281C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-014  ·  F2 · stair
- 位置：2F 楼梯预警（II-B3-02#ST 前约 4m）
- 坐标(pt)：(-24.265, 10.25)　吸附偏移：0.07m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-014
AT+BEACON=004C,0002,281D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-015  ·  F2 · elevator
- 位置：2F 电梯口（II-01#EL）· 门套或呼梯板旁
- 坐标(pt)：(-28.955, -74.887)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-015
AT+BEACON=004C,0002,281E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-016  ·  F2 · elevator
- 位置：2F 电梯口（II-02#EL）· 门套或呼梯板旁
- 坐标(pt)：(-62.946, -16.105)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-016
AT+BEACON=004C,0002,281F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-017  ·  F2 · elevator
- 位置：2F 电梯口（II-03#EL）· 门套或呼梯板旁
- 坐标(pt)：(-37.351, 9.084)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-017
AT+BEACON=004C,0002,2820,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-018  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-40.925, 1.958)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-018
AT+BEACON=004C,0002,2821,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-019  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-25.728, 1.958)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-019
AT+BEACON=004C,0002,2822,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-020  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-32.576, 1.958)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-020
AT+BEACON=004C,0002,2823,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-021  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-17.386, 1.958)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-021
AT+BEACON=004C,0002,2824,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-022  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-26.403, -4.266)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-022
AT+BEACON=004C,0002,2825,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-023  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-31.901, -4.266)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-023
AT+BEACON=004C,0002,2826,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-024  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-58.391, -2.469)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-024
AT+BEACON=004C,0002,2827,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-025  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-76.162, -23.237)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-025
AT+BEACON=004C,0002,2828,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-026  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-82.911, -23.237)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-026
AT+BEACON=004C,0002,2829,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-027  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-67.763, -23.237)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-027
AT+BEACON=004C,0002,282A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-028  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-59.371, -23.237)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-028
AT+BEACON=004C,0002,282B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-029  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-50.973, -23.237)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-029
AT+BEACON=004C,0002,282C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-030  ·  F2 · door
- 位置：2F 门口（门洞）
- 坐标(pt)：(-14.632, -66.873)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-030
AT+BEACON=004C,0002,282D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-031  ·  F2 · door
- 位置：2F 门口（门洞）
- 坐标(pt)：(-14.681, -71.672)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-031
AT+BEACON=004C,0002,282E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-032  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-16.405, -64.547)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-032
AT+BEACON=004C,0002,282F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-033  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-16.405, -57.798)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-033
AT+BEACON=004C,0002,2830,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-034  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-24.804, 19.424)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-034
AT+BEACON=004C,0002,2831,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-035  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-24.804, 26.173)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-035
AT+BEACON=004C,0002,2832,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-036  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-41.899, 16.225)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-036
AT+BEACON=004C,0002,2833,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-037  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-41.899, 26.173)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-037
AT+BEACON=004C,0002,2834,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-038  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-75.486, 0.978)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-038
AT+BEACON=004C,0002,2835,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-039  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-75.486, -9.764)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-039
AT+BEACON=004C,0002,2836,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-040  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-40.925, -82.318)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-040
AT+BEACON=004C,0002,2837,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-041  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-32.379, -82.318)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-041
AT+BEACON=004C,0002,2838,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-042  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-29.676, -82.318)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-042
AT+BEACON=004C,0002,2839,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-043  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-8.987, -82.318)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-043
AT+BEACON=004C,0002,283A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-044  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-17.533, -82.318)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-044
AT+BEACON=004C,0002,283B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-045  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-20.229, -82.318)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-045
AT+BEACON=004C,0002,283C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-046  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-49.426, -21.729)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-046
AT+BEACON=004C,0002,283D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-047  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-34.067, -60.477)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-047
AT+BEACON=004C,0002,283E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-048  ·  F2 · door
- 位置：2F 门口（门洞）
- 坐标(pt)：(-23.031, 17.098)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-048
AT+BEACON=004C,0002,283F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-049  ·  F2 · door
- 位置：2F 门口（防火门）
- 坐标(pt)：(-80.565, -22.412)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-049
AT+BEACON=004C,0002,2840,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-050  ·  F2 · door
- 位置：2F 门口（防火门）
- 坐标(pt)：(-70.109, -22.412)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-050
AT+BEACON=004C,0002,2841,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-051  ·  F2 · door
- 位置：2F 门口（防火门）
- 坐标(pt)：(-16.38, -60.704)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-051
AT+BEACON=004C,0002,2842,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-052  ·  F2 · door
- 位置：2F 门口（普通门）
- 坐标(pt)：(-24.509, 12.347)　吸附偏移：0.0m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-052
AT+BEACON=004C,0002,2843,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-053  ·  F2 · elevator
- 位置：2F 电梯厅 · 优先柱/侧墙
- 坐标(pt)：(-41.299, 5.802)　吸附偏移：0.41m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-053
AT+BEACON=004C,0002,2844,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-054  ·  F2 · intersection
- 位置：2F 交叉口（交叉口1）
- 坐标(pt)：(-45.247, -6.646)　吸附偏移：1.14m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-054
AT+BEACON=004C,0002,2845,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-055  ·  F2 · intersection
- 位置：2F 交叉口（交叉口3）
- 坐标(pt)：(-28.203, 5.802)　吸附偏移：1.39m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-055
AT+BEACON=004C,0002,2846,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-056  ·  F2 · intersection
- 位置：2F 交叉口（交叉口5）
- 坐标(pt)：(-61.797, -19.393)　吸附偏移：1.46m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-056
AT+BEACON=004C,0002,2847,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-057  ·  F2 · intersection
- 位置：2F 交叉口（交叉口8）
- 坐标(pt)：(-83.786, -20.231)　吸附偏移：0.82m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-057
AT+BEACON=004C,0002,2848,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-058  ·  F2 · intersection
- 位置：2F 交叉口（交叉口11）
- 坐标(pt)：(-18.859, -56.929)　吸附偏移：1.64m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-058
AT+BEACON=004C,0002,2849,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-059  ·  F2 · intersection
- 位置：2F 交叉口（交叉口12）
- 坐标(pt)：(-32.907, -78.169)　吸附偏移：1.69m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-059
AT+BEACON=004C,0002,284A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-060  ·  F2 · intersection
- 位置：2F 交叉口（交叉口13）
- 坐标(pt)：(-31.053, -56.929)　吸附偏移：1.48m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-060
AT+BEACON=004C,0002,284B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-061  ·  F2 · intersection
- 位置：2F 交叉口（交叉口14）
- 坐标(pt)：(-19.811, -78.169)　吸附偏移：1.82m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-061
AT+BEACON=004C,0002,284C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-062  ·  F2 · intersection
- 位置：2F 交叉口（交叉口16）
- 坐标(pt)：(-74.893, -6.806)　吸附偏移：7.71m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-062
AT+BEACON=004C,0002,284D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-063  ·  F2 · intersection
- 位置：2F 交叉口（交叉口17）
- 坐标(pt)：(-24.705, -73.523)　吸附偏移：2.05m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-063
AT+BEACON=004C,0002,284E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-064  ·  F2 · intersection
- 位置：2F 交叉口（交叉口18）
- 坐标(pt)：(-33.104, -65.324)　吸附偏移：7.98m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-064
AT+BEACON=004C,0002,284F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-065  ·  F2 · intersection
- 位置：2F 交叉口（交叉口20）
- 坐标(pt)：(-17.012, -71.624)　吸附偏移：1.36m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-065
AT+BEACON=004C,0002,2850,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-066  ·  F2 · intersection
- 位置：2F 交叉口（交叉口27）
- 坐标(pt)：(-28.203, 10.449)　吸附偏移：1.86m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-066
AT+BEACON=004C,0002,2851,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-067  ·  F2 · intersection
- 位置：2F 交叉口（交叉口28）
- 坐标(pt)：(-27.403, 27.042)　吸附偏移：1.99m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-067
AT+BEACON=004C,0002,2852,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-068  ·  F2 · intersection
- 位置：2F 交叉口（交叉口29）
- 坐标(pt)：(-33.605, 26.744)　吸附偏移：1.58m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-068
AT+BEACON=004C,0002,2853,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-069  ·  F2 · intersection
- 位置：2F 交叉口（交叉口30）
- 坐标(pt)：(-33.605, 10.449)　吸附偏移：1.69m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-069
AT+BEACON=004C,0002,2854,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-070  ·  F2 · intersection
- 位置：2F 交叉口（交叉口31）
- 坐标(pt)：(-41.498, 18.35)　吸附偏移：1.84m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-070
AT+BEACON=004C,0002,2855,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-071  ·  F2 · intersection
- 位置：2F 交叉口（交叉口34）
- 坐标(pt)：(-16.51, 4.964)　吸附偏移：1.29m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-071
AT+BEACON=004C,0002,2856,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-072  ·  F2 · intersection
- 位置：2F 交叉口（交叉口36）
- 坐标(pt)：(-72.093, -14.74)　吸附偏移：1.75m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-072
AT+BEACON=004C,0002,2857,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-073  ·  F2 · intersection
- 位置：2F 交叉口（交叉口38）
- 坐标(pt)：(-58.794, -6.843)　吸附偏移：1.95m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-073
AT+BEACON=004C,0002,2858,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-074  ·  F2 · intersection
- 位置：2F 交叉口（交叉口39）
- 坐标(pt)：(-67.193, 1.555)　吸附偏移：1.87m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-074
AT+BEACON=004C,0002,2859,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-075  ·  F2 · intersection
- 位置：2F 交叉口（交叉口40）
- 坐标(pt)：(-73.991, -21.786)　吸附偏移：1.51m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-075
AT+BEACON=004C,0002,285A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-076  ·  F2 · intersection
- 位置：2F 交叉口（交叉口41）
- 坐标(pt)：(-32.907, -72.596)　吸附偏移：1.55m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-076
AT+BEACON=004C,0002,285B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-077  ·  F2 · intersection
- 位置：2F 交叉口（交叉口43）
- 坐标(pt)：(-25.207, -57.227)　吸附偏移：1.25m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-077
AT+BEACON=004C,0002,285C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-078  ·  F2 · intersection
- 位置：2F 交叉口（交叉口45）
- 坐标(pt)：(-34.067, -58.269)　吸附偏移：0.71m　安装高度：2.2m　方式：door_frame
```
AT+NAME=BK-02-078
AT+BEACON=004C,0002,285D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-079  ·  F2 · intersection
- 位置：2F 交叉口（交叉口1 · 北向）
- 坐标(pt)：(-42.004, -3.738)　吸附偏移：2.37m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-079
AT+BEACON=004C,0002,285E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-080  ·  F2 · intersection
- 位置：2F 交叉口（交叉口2 · 东向）
- 坐标(pt)：(-38.055, 27.042)　吸附偏移：1.65m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-080
AT+BEACON=004C,0002,285F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-081  ·  F2 · intersection
- 位置：2F 交叉口（交叉口3 · 东向）
- 坐标(pt)：(-25.41, 5.802)　吸附偏移：0.99m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-081
AT+BEACON=004C,0002,2860,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-082  ·  F2 · intersection
- 位置：2F 交叉口（交叉口5 · 西向）
- 坐标(pt)：(-62.946, -21.939)　吸附偏移：1.17m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-082
AT+BEACON=004C,0002,2861,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-083  ·  F2 · intersection
- 位置：2F 交叉口（交叉口7 · 北向）
- 坐标(pt)：(-58.845, -10.995)　吸附偏移：1.71m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-083
AT+BEACON=004C,0002,2862,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-084  ·  F2 · intersection
- 位置：2F 交叉口（交叉口9 · 西向）
- 坐标(pt)：(-53.049, -21.939)　吸附偏移：1.12m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-084
AT+BEACON=004C,0002,2863,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-085  ·  F2 · intersection
- 位置：2F 交叉口（交叉口11 · 西向）
- 坐标(pt)：(-21.335, -56.929)　吸附偏移：1.68m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-085
AT+BEACON=004C,0002,2864,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-086  ·  F2 · intersection
- 位置：2F 交叉口（交叉口12 · 东向）
- 坐标(pt)：(-30.108, -78.169)　吸附偏移：1.22m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-086
AT+BEACON=004C,0002,2865,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-087  ·  F2 · intersection
- 位置：2F 交叉口（交叉口16 · 东向）
- 坐标(pt)：(-65.742, -14.74)　吸附偏移：6.55m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-087
AT+BEACON=004C,0002,2866,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-088  ·  F2 · intersection
- 位置：2F 交叉口（交叉口17 · 东向）
- 坐标(pt)：(-22.655, -73.719)　吸附偏移：2.21m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-088
AT+BEACON=004C,0002,2867,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-089  ·  F2 · intersection
- 位置：2F 交叉口（交叉口18 · 东向）
- 坐标(pt)：(-17.012, -66.685)　吸附偏移：6.65m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-089
AT+BEACON=004C,0002,2868,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-090  ·  F2 · intersection
- 位置：2F 交叉口（交叉口20 · 北向）
- 坐标(pt)：(-16.91, -69.327)　吸附偏移：1.52m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-090
AT+BEACON=004C,0002,2869,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-091  ·  F2 · intersection
- 位置：2F 交叉口（交叉口21 · 北向）
- 坐标(pt)：(-33.402, -63.023)　吸附偏移：0.64m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-091
AT+BEACON=004C,0002,286A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-092  ·  F2 · intersection
- 位置：2F 交叉口（交叉口21 · 南向）
- 坐标(pt)：(-33.402, -67.797)　吸附偏移：1.43m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-092
AT+BEACON=004C,0002,286B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-093  ·  F2 · intersection
- 位置：2F 交叉口（交叉口28 · 南向）
- 坐标(pt)：(-25.41, 23.599)　吸附偏移：3.38m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-093
AT+BEACON=004C,0002,286C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-094  ·  F2 · intersection
- 位置：2F 交叉口（交叉口28 · 西向）
- 坐标(pt)：(-29.708, 27.042)　吸附偏移：1.98m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-094
AT+BEACON=004C,0002,286D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-095  ·  F2 · intersection
- 位置：2F 交叉口（交叉口29 · 西向）
- 坐标(pt)：(-35.802, 27.042)　吸附偏移：1.78m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-095
AT+BEACON=004C,0002,286E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-096  ·  F2 · intersection
- 位置：2F 交叉口（交叉口30 · 东向）
- 坐标(pt)：(-31.053, 10.252)　吸附偏移：1.93m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-096
AT+BEACON=004C,0002,286F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-097  ·  F2 · intersection
- 位置：2F 交叉口（交叉口32 · 南向）
- 坐标(pt)：(-25.41, 17.146)　吸附偏移：2.0m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-097
AT+BEACON=004C,0002,2870,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-098  ·  F2 · intersection
- 位置：2F 交叉口（交叉口33 · 东向）
- 坐标(pt)：(-38.5, 5.802)　吸附偏移：1.41m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-098
AT+BEACON=004C,0002,2871,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-099  ·  F2 · intersection
- 位置：2F 交叉口（交叉口34 · 西向）
- 坐标(pt)：(-19.957, 6.0)　吸附偏移：1.28m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-099
AT+BEACON=004C,0002,2872,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-100  ·  F2 · intersection
- 位置：2F 交叉口（交叉口35 · 南向）
- 坐标(pt)：(-74.893, -1.776)　吸附偏移：3.06m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-100
AT+BEACON=004C,0002,2873,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-101  ·  F2 · intersection
- 位置：2F 交叉口（交叉口35 · 东向）
- 坐标(pt)：(-71.719, 1.854)　吸附偏移：2.23m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-101
AT+BEACON=004C,0002,2874,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-102  ·  F2 · intersection
- 位置：2F 交叉口（交叉口39 · 东向）
- 坐标(pt)：(-64.495, 1.854)　吸附偏移：2.1m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-102
AT+BEACON=004C,0002,2875,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-103  ·  F2 · intersection
- 位置：2F 交叉口（交叉口40 · 北向）
- 坐标(pt)：(-72.093, -18.892)　吸附偏移：1.07m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-103
AT+BEACON=004C,0002,2876,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-104  ·  F2 · intersection
- 位置：2F 交叉口（交叉口41 · 东向）
- 坐标(pt)：(-33.307, -70.187)　吸附偏移：3.01m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-104
AT+BEACON=004C,0002,2877,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-105  ·  F2 · intersection
- 位置：2F 交叉口（交叉口43 · 西向）
- 坐标(pt)：(-27.403, -56.929)　吸附偏移：1.54m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-105
AT+BEACON=004C,0002,2878,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-106  ·  F2 · intersection
- 位置：2F 交叉口（交叉口47 · 西向）
- 坐标(pt)：(-34.703, 5.802)　吸附偏移：1.38m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-106
AT+BEACON=004C,0002,2879,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-107  ·  F2 · intersection
- 位置：2F 交叉口（交叉口51 · 西向）
- 坐标(pt)：(-77.56, -21.786)　吸附偏移：0.85m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-107
AT+BEACON=004C,0002,287A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-108  ·  F2 · intersection
- 位置：2F 交叉口（交叉口52 · 东向）
- 坐标(pt)：(-65.593, -21.786)　吸附偏移：0.95m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-108
AT+BEACON=004C,0002,287B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-02-109  ·  F2 · intersection
- 位置：2F 交叉口（交叉口53 · 东向）
- 坐标(pt)：(-57.194, -21.786)　吸附偏移：0.92m　安装高度：2.2m　方式：wall
```
AT+NAME=BK-02-109
AT+BEACON=004C,0002,287C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-001  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-16.793539830503804, -33.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-001
AT+BEACON=004C,0001,287D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-002  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-29.793539830503804, -66.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-002
AT+BEACON=004C,0001,287E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-003  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-31.793539830503804, -66.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-003
AT+BEACON=004C,0001,287F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-004  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-15.793539830503804, -31.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-004
AT+BEACON=004C,0001,2880,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-005  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-54.7935398305038, -49.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-005
AT+BEACON=004C,0001,2881,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-006  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-47.7935398305038, -0.17538395577955157)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-006
AT+BEACON=004C,0001,2882,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-007  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-55.7935398305038, -41.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-007
AT+BEACON=004C,0001,2883,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-008  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-21.793539830503804, -61.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-008
AT+BEACON=004C,0001,2884,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-009  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-38.7935398305038, -43.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-009
AT+BEACON=004C,0001,2885,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-010  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-24.793539830503804, -32.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-010
AT+BEACON=004C,0001,2886,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-011  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-30.793539830503804, -58.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-011
AT+BEACON=004C,0001,2887,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-012  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-56.7935398305038, -41.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-012
AT+BEACON=004C,0001,2888,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-013  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-39.7935398305038, -43.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-013
AT+BEACON=004C,0001,2889,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-014  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-18.793539830503804, -61.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-014
AT+BEACON=004C,0001,288A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-015  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-56.7935398305038, -13.175383955779552)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-015
AT+BEACON=004C,0001,288B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-016  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-48.7935398305038, -1.1753839557795516)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-016
AT+BEACON=004C,0001,288C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-017  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-15.793539830503804, -40.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-017
AT+BEACON=004C,0001,288D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-018  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-9.793539830503804, -28.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-018
AT+BEACON=004C,0001,288E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-019  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-27.793539830503804, -73.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-019
AT+BEACON=004C,0001,288F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-020  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-39.7935398305038, -31.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-020
AT+BEACON=004C,0001,2890,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-021  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-23.793539830503804, -40.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-021
AT+BEACON=004C,0001,2891,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-022  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-57.7935398305038, -56.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-022
AT+BEACON=004C,0001,2892,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-023  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-56.7935398305038, -41.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-023
AT+BEACON=004C,0001,2893,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-024  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-48.7935398305038, -3.1753839557795516)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-024
AT+BEACON=004C,0001,2894,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-025  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-41.7935398305038, -26.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-025
AT+BEACON=004C,0001,2895,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-026  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-36.7935398305038, -59.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-026
AT+BEACON=004C,0001,2896,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-027  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-8.793539830503804, -30.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-027
AT+BEACON=004C,0001,2897,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-028  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-57.7935398305038, -33.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-028
AT+BEACON=004C,0001,2898,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-029  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-15.793539830503804, -55.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-029
AT+BEACON=004C,0001,2899,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-030  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-57.7935398305038, -56.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-030
AT+BEACON=004C,0001,289A,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-031  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-57.7935398305038, -33.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-031
AT+BEACON=004C,0001,289B,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-032  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-28.793539830503804, 26.82461604422045)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-032
AT+BEACON=004C,0001,289C,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-033  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-82.7935398305038, -19.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-033
AT+BEACON=004C,0001,289D,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-034  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-57.7935398305038, -32.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-034
AT+BEACON=004C,0001,289E,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-035  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-49.7935398305038, 6.824616044220448)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-035
AT+BEACON=004C,0001,289F,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-036  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-23.793539830503804, -30.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-036
AT+BEACON=004C,0001,28A0,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-037  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-17.793539830503804, 8.824616044220448)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-037
AT+BEACON=004C,0001,28A1,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-038  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-17.793539830503804, 8.824616044220448)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-038
AT+BEACON=004C,0001,28A2,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-039  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-59.7935398305038, -1.1753839557795516)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-039
AT+BEACON=004C,0001,28A3,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-040  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-49.7935398305038, 7.824616044220448)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-040
AT+BEACON=004C,0001,28A4,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-041  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-39.7935398305038, -46.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-041
AT+BEACON=004C,0001,28A5,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-042  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-38.7935398305038, 6.824616044220448)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-042
AT+BEACON=004C,0001,28A6,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-043  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-36.7935398305038, -73.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-043
AT+BEACON=004C,0001,28A7,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-044  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-36.7935398305038, -73.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-044
AT+BEACON=004C,0001,28A8,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-045  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-24.793539830503804, -82.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-045
AT+BEACON=004C,0001,28A9,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-046  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-24.793539830503804, -82.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-046
AT+BEACON=004C,0001,28AA,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-047  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-24.793539830503804, -82.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-047
AT+BEACON=004C,0001,28AB,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-048  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-23.793539830503804, -29.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-048
AT+BEACON=004C,0001,28AC,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F1-049  ·  F1 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-15.793539830503804, -88.17538395577955)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F1-049
AT+BEACON=004C,0001,28AD,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-001  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-50.03175198707004, -1.270788174641936)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-001
AT+BEACON=004C,0002,28AE,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-002  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-57.03175198707004, 1.729211825358064)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-002
AT+BEACON=004C,0002,28AF,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-003  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-57.03175198707004, 1.729211825358064)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-003
AT+BEACON=004C,0002,28B0,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-004  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-38.03175198707004, 10.729211825358064)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-004
AT+BEACON=004C,0002,28B1,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-005  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-16.031751987070038, -80.27078817464194)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-005
AT+BEACON=004C,0002,28B2,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-006  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-16.031751987070038, -80.27078817464194)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-006
AT+BEACON=004C,0002,28B3,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-007  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-62.03175198707004, -14.270788174641936)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-007
AT+BEACON=004C,0002,28B4,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-008  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-43.03175198707004, -6.270788174641936)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-008
AT+BEACON=004C,0002,28B5,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-009  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-66.03175198707004, -22.270788174641936)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-009
AT+BEACON=004C,0002,28B6,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-010  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-34.03175198707004, -80.27078817464194)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-010
AT+BEACON=004C,0002,28B7,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

### BK-T-F2-011  ·  F2 · trilateration_fill
- 位置：房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)
- 坐标(pt)：(-10.031751987070038, -80.27078817464194)　吸附偏移：0.0m　安装高度：2.2m　方式：interior
```
AT+NAME=BK-T-F2-011
AT+BEACON=004C,0002,28B8,C5,B9407F30F5F8466EAFF925556B57FE6D
AT+POWER=-10
AT+ADS=,1,300
```

## 二、命令汇总表

| # | beaconId | 楼层 | Major(hex) | Minor(dec) | Minor(hex) | RSSI@1m(hex) | 间隔(ms) | AT+BEACON 命令 |
|---|---|---|---|---|---|---|---|---|
| 1 | BK-01-001 | F1 | 0001 | 10101 | 2775 | C5 | 300 | `AT+BEACON=004C,0001,2775,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 2 | BK-01-002 | F1 | 0001 | 10102 | 2776 | C5 | 300 | `AT+BEACON=004C,0001,2776,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 3 | BK-01-003 | F1 | 0001 | 10103 | 2777 | C5 | 300 | `AT+BEACON=004C,0001,2777,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 4 | BK-01-004 | F1 | 0001 | 10104 | 2778 | C5 | 300 | `AT+BEACON=004C,0001,2778,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 5 | BK-01-005 | F1 | 0001 | 10105 | 2779 | C5 | 300 | `AT+BEACON=004C,0001,2779,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 6 | BK-01-006 | F1 | 0001 | 10106 | 277A | C5 | 300 | `AT+BEACON=004C,0001,277A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 7 | BK-01-007 | F1 | 0001 | 10107 | 277B | C5 | 300 | `AT+BEACON=004C,0001,277B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 8 | BK-01-008 | F1 | 0001 | 10108 | 277C | C5 | 300 | `AT+BEACON=004C,0001,277C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 9 | BK-01-009 | F1 | 0001 | 10109 | 277D | C5 | 300 | `AT+BEACON=004C,0001,277D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 10 | BK-01-010 | F1 | 0001 | 10110 | 277E | C5 | 300 | `AT+BEACON=004C,0001,277E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 11 | BK-01-011 | F1 | 0001 | 10111 | 277F | C5 | 300 | `AT+BEACON=004C,0001,277F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 12 | BK-01-012 | F1 | 0001 | 10112 | 2780 | C5 | 300 | `AT+BEACON=004C,0001,2780,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 13 | BK-01-013 | F1 | 0001 | 10113 | 2781 | C5 | 300 | `AT+BEACON=004C,0001,2781,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 14 | BK-01-014 | F1 | 0001 | 10114 | 2782 | C5 | 300 | `AT+BEACON=004C,0001,2782,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 15 | BK-01-015 | F1 | 0001 | 10115 | 2783 | C5 | 300 | `AT+BEACON=004C,0001,2783,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 16 | BK-01-016 | F1 | 0001 | 10116 | 2784 | C5 | 300 | `AT+BEACON=004C,0001,2784,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 17 | BK-01-017 | F1 | 0001 | 10117 | 2785 | C5 | 300 | `AT+BEACON=004C,0001,2785,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 18 | BK-01-018 | F1 | 0001 | 10118 | 2786 | C5 | 300 | `AT+BEACON=004C,0001,2786,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 19 | BK-01-019 | F1 | 0001 | 10119 | 2787 | C5 | 300 | `AT+BEACON=004C,0001,2787,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 20 | BK-01-020 | F1 | 0001 | 10120 | 2788 | C5 | 300 | `AT+BEACON=004C,0001,2788,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 21 | BK-01-021 | F1 | 0001 | 10121 | 2789 | C5 | 300 | `AT+BEACON=004C,0001,2789,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 22 | BK-01-022 | F1 | 0001 | 10122 | 278A | C5 | 300 | `AT+BEACON=004C,0001,278A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 23 | BK-01-023 | F1 | 0001 | 10123 | 278B | C5 | 300 | `AT+BEACON=004C,0001,278B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 24 | BK-01-024 | F1 | 0001 | 10124 | 278C | C5 | 300 | `AT+BEACON=004C,0001,278C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 25 | BK-01-025 | F1 | 0001 | 10125 | 278D | C5 | 300 | `AT+BEACON=004C,0001,278D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 26 | BK-01-026 | F1 | 0001 | 10126 | 278E | C5 | 300 | `AT+BEACON=004C,0001,278E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 27 | BK-01-027 | F1 | 0001 | 10127 | 278F | C5 | 300 | `AT+BEACON=004C,0001,278F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 28 | BK-01-028 | F1 | 0001 | 10128 | 2790 | C5 | 300 | `AT+BEACON=004C,0001,2790,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 29 | BK-01-029 | F1 | 0001 | 10129 | 2791 | C5 | 300 | `AT+BEACON=004C,0001,2791,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 30 | BK-01-030 | F1 | 0001 | 10130 | 2792 | C5 | 300 | `AT+BEACON=004C,0001,2792,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 31 | BK-01-031 | F1 | 0001 | 10131 | 2793 | C5 | 300 | `AT+BEACON=004C,0001,2793,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 32 | BK-01-032 | F1 | 0001 | 10132 | 2794 | C5 | 300 | `AT+BEACON=004C,0001,2794,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 33 | BK-01-033 | F1 | 0001 | 10133 | 2795 | C5 | 300 | `AT+BEACON=004C,0001,2795,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 34 | BK-01-034 | F1 | 0001 | 10134 | 2796 | C5 | 300 | `AT+BEACON=004C,0001,2796,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 35 | BK-01-035 | F1 | 0001 | 10135 | 2797 | C5 | 300 | `AT+BEACON=004C,0001,2797,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 36 | BK-01-036 | F1 | 0001 | 10136 | 2798 | C5 | 300 | `AT+BEACON=004C,0001,2798,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 37 | BK-01-037 | F1 | 0001 | 10137 | 2799 | C5 | 300 | `AT+BEACON=004C,0001,2799,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 38 | BK-01-038 | F1 | 0001 | 10138 | 279A | C5 | 300 | `AT+BEACON=004C,0001,279A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 39 | BK-01-039 | F1 | 0001 | 10139 | 279B | C5 | 300 | `AT+BEACON=004C,0001,279B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 40 | BK-01-040 | F1 | 0001 | 10140 | 279C | C5 | 300 | `AT+BEACON=004C,0001,279C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 41 | BK-01-041 | F1 | 0001 | 10141 | 279D | C5 | 300 | `AT+BEACON=004C,0001,279D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 42 | BK-01-042 | F1 | 0001 | 10142 | 279E | C5 | 300 | `AT+BEACON=004C,0001,279E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 43 | BK-01-043 | F1 | 0001 | 10143 | 279F | C5 | 300 | `AT+BEACON=004C,0001,279F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 44 | BK-01-044 | F1 | 0001 | 10144 | 27A0 | C5 | 300 | `AT+BEACON=004C,0001,27A0,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 45 | BK-01-045 | F1 | 0001 | 10145 | 27A1 | C5 | 300 | `AT+BEACON=004C,0001,27A1,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 46 | BK-01-046 | F1 | 0001 | 10146 | 27A2 | C5 | 300 | `AT+BEACON=004C,0001,27A2,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 47 | BK-01-047 | F1 | 0001 | 10147 | 27A3 | C5 | 300 | `AT+BEACON=004C,0001,27A3,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 48 | BK-01-048 | F1 | 0001 | 10148 | 27A4 | C5 | 300 | `AT+BEACON=004C,0001,27A4,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 49 | BK-01-049 | F1 | 0001 | 10149 | 27A5 | C5 | 300 | `AT+BEACON=004C,0001,27A5,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 50 | BK-01-050 | F1 | 0001 | 10150 | 27A6 | C5 | 300 | `AT+BEACON=004C,0001,27A6,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 51 | BK-01-051 | F1 | 0001 | 10151 | 27A7 | C5 | 300 | `AT+BEACON=004C,0001,27A7,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 52 | BK-01-052 | F1 | 0001 | 10152 | 27A8 | C5 | 300 | `AT+BEACON=004C,0001,27A8,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 53 | BK-01-053 | F1 | 0001 | 10153 | 27A9 | C5 | 300 | `AT+BEACON=004C,0001,27A9,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 54 | BK-01-054 | F1 | 0001 | 10154 | 27AA | C5 | 300 | `AT+BEACON=004C,0001,27AA,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 55 | BK-01-055 | F1 | 0001 | 10155 | 27AB | C5 | 300 | `AT+BEACON=004C,0001,27AB,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 56 | BK-01-056 | F1 | 0001 | 10156 | 27AC | C5 | 300 | `AT+BEACON=004C,0001,27AC,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 57 | BK-01-057 | F1 | 0001 | 10157 | 27AD | C5 | 300 | `AT+BEACON=004C,0001,27AD,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 58 | BK-01-058 | F1 | 0001 | 10158 | 27AE | C5 | 300 | `AT+BEACON=004C,0001,27AE,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 59 | BK-01-059 | F1 | 0001 | 10159 | 27AF | C5 | 300 | `AT+BEACON=004C,0001,27AF,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 60 | BK-01-060 | F1 | 0001 | 10160 | 27B0 | C5 | 300 | `AT+BEACON=004C,0001,27B0,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 61 | BK-01-061 | F1 | 0001 | 10161 | 27B1 | C5 | 300 | `AT+BEACON=004C,0001,27B1,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 62 | BK-01-062 | F1 | 0001 | 10162 | 27B2 | C5 | 300 | `AT+BEACON=004C,0001,27B2,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 63 | BK-01-063 | F1 | 0001 | 10163 | 27B3 | C5 | 300 | `AT+BEACON=004C,0001,27B3,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 64 | BK-01-064 | F1 | 0001 | 10164 | 27B4 | C5 | 300 | `AT+BEACON=004C,0001,27B4,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 65 | BK-01-065 | F1 | 0001 | 10165 | 27B5 | C5 | 300 | `AT+BEACON=004C,0001,27B5,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 66 | BK-01-066 | F1 | 0001 | 10166 | 27B6 | C5 | 300 | `AT+BEACON=004C,0001,27B6,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 67 | BK-01-067 | F1 | 0001 | 10167 | 27B7 | C5 | 300 | `AT+BEACON=004C,0001,27B7,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 68 | BK-01-068 | F1 | 0001 | 10168 | 27B8 | C5 | 300 | `AT+BEACON=004C,0001,27B8,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 69 | BK-01-069 | F1 | 0001 | 10169 | 27B9 | C5 | 300 | `AT+BEACON=004C,0001,27B9,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 70 | BK-01-070 | F1 | 0001 | 10170 | 27BA | C5 | 300 | `AT+BEACON=004C,0001,27BA,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 71 | BK-01-071 | F1 | 0001 | 10171 | 27BB | C5 | 300 | `AT+BEACON=004C,0001,27BB,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 72 | BK-01-072 | F1 | 0001 | 10172 | 27BC | C5 | 300 | `AT+BEACON=004C,0001,27BC,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 73 | BK-01-073 | F1 | 0001 | 10173 | 27BD | C5 | 300 | `AT+BEACON=004C,0001,27BD,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 74 | BK-01-074 | F1 | 0001 | 10174 | 27BE | C5 | 300 | `AT+BEACON=004C,0001,27BE,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 75 | BK-01-075 | F1 | 0001 | 10175 | 27BF | C5 | 300 | `AT+BEACON=004C,0001,27BF,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 76 | BK-01-076 | F1 | 0001 | 10176 | 27C0 | C5 | 300 | `AT+BEACON=004C,0001,27C0,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 77 | BK-01-077 | F1 | 0001 | 10177 | 27C1 | C5 | 300 | `AT+BEACON=004C,0001,27C1,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 78 | BK-01-078 | F1 | 0001 | 10178 | 27C2 | C5 | 300 | `AT+BEACON=004C,0001,27C2,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 79 | BK-01-079 | F1 | 0001 | 10179 | 27C3 | C5 | 300 | `AT+BEACON=004C,0001,27C3,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 80 | BK-01-080 | F1 | 0001 | 10180 | 27C4 | C5 | 300 | `AT+BEACON=004C,0001,27C4,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 81 | BK-01-081 | F1 | 0001 | 10181 | 27C5 | C5 | 300 | `AT+BEACON=004C,0001,27C5,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 82 | BK-01-082 | F1 | 0001 | 10182 | 27C6 | C5 | 300 | `AT+BEACON=004C,0001,27C6,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 83 | BK-01-083 | F1 | 0001 | 10183 | 27C7 | C5 | 300 | `AT+BEACON=004C,0001,27C7,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 84 | BK-01-084 | F1 | 0001 | 10184 | 27C8 | C5 | 300 | `AT+BEACON=004C,0001,27C8,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 85 | BK-01-085 | F1 | 0001 | 10185 | 27C9 | C5 | 300 | `AT+BEACON=004C,0001,27C9,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 86 | BK-01-086 | F1 | 0001 | 10186 | 27CA | C5 | 300 | `AT+BEACON=004C,0001,27CA,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 87 | BK-01-087 | F1 | 0001 | 10187 | 27CB | C5 | 300 | `AT+BEACON=004C,0001,27CB,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 88 | BK-01-088 | F1 | 0001 | 10188 | 27CC | C5 | 300 | `AT+BEACON=004C,0001,27CC,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 89 | BK-01-089 | F1 | 0001 | 10189 | 27CD | C5 | 300 | `AT+BEACON=004C,0001,27CD,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 90 | BK-01-090 | F1 | 0001 | 10190 | 27CE | C5 | 300 | `AT+BEACON=004C,0001,27CE,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 91 | BK-01-091 | F1 | 0001 | 10191 | 27CF | C5 | 300 | `AT+BEACON=004C,0001,27CF,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 92 | BK-01-092 | F1 | 0001 | 10192 | 27D0 | C5 | 300 | `AT+BEACON=004C,0001,27D0,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 93 | BK-01-093 | F1 | 0001 | 10193 | 27D1 | C5 | 300 | `AT+BEACON=004C,0001,27D1,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 94 | BK-01-094 | F1 | 0001 | 10194 | 27D2 | C5 | 300 | `AT+BEACON=004C,0001,27D2,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 95 | BK-01-095 | F1 | 0001 | 10195 | 27D3 | C5 | 300 | `AT+BEACON=004C,0001,27D3,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 96 | BK-01-096 | F1 | 0001 | 10196 | 27D4 | C5 | 300 | `AT+BEACON=004C,0001,27D4,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 97 | BK-01-097 | F1 | 0001 | 10197 | 27D5 | C5 | 300 | `AT+BEACON=004C,0001,27D5,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 98 | BK-01-098 | F1 | 0001 | 10198 | 27D6 | C5 | 300 | `AT+BEACON=004C,0001,27D6,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 99 | BK-01-099 | F1 | 0001 | 10199 | 27D7 | C5 | 300 | `AT+BEACON=004C,0001,27D7,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 100 | BK-01-100 | F1 | 0001 | 10200 | 27D8 | C5 | 300 | `AT+BEACON=004C,0001,27D8,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 101 | BK-01-101 | F1 | 0001 | 10201 | 27D9 | C5 | 300 | `AT+BEACON=004C,0001,27D9,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 102 | BK-01-102 | F1 | 0001 | 10202 | 27DA | C5 | 300 | `AT+BEACON=004C,0001,27DA,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 103 | BK-01-103 | F1 | 0001 | 10203 | 27DB | C5 | 300 | `AT+BEACON=004C,0001,27DB,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 104 | BK-01-104 | F1 | 0001 | 10204 | 27DC | C5 | 300 | `AT+BEACON=004C,0001,27DC,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 105 | BK-01-105 | F1 | 0001 | 10205 | 27DD | C5 | 300 | `AT+BEACON=004C,0001,27DD,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 106 | BK-01-106 | F1 | 0001 | 10206 | 27DE | C5 | 300 | `AT+BEACON=004C,0001,27DE,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 107 | BK-01-107 | F1 | 0001 | 10207 | 27DF | C5 | 300 | `AT+BEACON=004C,0001,27DF,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 108 | BK-01-108 | F1 | 0001 | 10208 | 27E0 | C5 | 300 | `AT+BEACON=004C,0001,27E0,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 109 | BK-01-109 | F1 | 0001 | 10209 | 27E1 | C5 | 300 | `AT+BEACON=004C,0001,27E1,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 110 | BK-01-110 | F1 | 0001 | 10210 | 27E2 | C5 | 300 | `AT+BEACON=004C,0001,27E2,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 111 | BK-01-111 | F1 | 0001 | 10211 | 27E3 | C5 | 300 | `AT+BEACON=004C,0001,27E3,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 112 | BK-01-112 | F1 | 0001 | 10212 | 27E4 | C5 | 300 | `AT+BEACON=004C,0001,27E4,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 113 | BK-01-113 | F1 | 0001 | 10213 | 27E5 | C5 | 300 | `AT+BEACON=004C,0001,27E5,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 114 | BK-01-114 | F1 | 0001 | 10214 | 27E6 | C5 | 300 | `AT+BEACON=004C,0001,27E6,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 115 | BK-01-115 | F1 | 0001 | 10215 | 27E7 | C5 | 300 | `AT+BEACON=004C,0001,27E7,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 116 | BK-01-116 | F1 | 0001 | 10216 | 27E8 | C5 | 300 | `AT+BEACON=004C,0001,27E8,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 117 | BK-01-117 | F1 | 0001 | 10217 | 27E9 | C5 | 300 | `AT+BEACON=004C,0001,27E9,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 118 | BK-01-118 | F1 | 0001 | 10218 | 27EA | C5 | 300 | `AT+BEACON=004C,0001,27EA,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 119 | BK-01-119 | F1 | 0001 | 10219 | 27EB | C5 | 300 | `AT+BEACON=004C,0001,27EB,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 120 | BK-01-120 | F1 | 0001 | 10220 | 27EC | C5 | 300 | `AT+BEACON=004C,0001,27EC,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 121 | BK-01-121 | F1 | 0001 | 10221 | 27ED | C5 | 300 | `AT+BEACON=004C,0001,27ED,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 122 | BK-01-122 | F1 | 0001 | 10222 | 27EE | C5 | 300 | `AT+BEACON=004C,0001,27EE,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 123 | BK-01-123 | F1 | 0001 | 10223 | 27EF | C5 | 300 | `AT+BEACON=004C,0001,27EF,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 124 | BK-01-124 | F1 | 0001 | 10224 | 27F0 | C5 | 300 | `AT+BEACON=004C,0001,27F0,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 125 | BK-01-125 | F1 | 0001 | 10225 | 27F1 | C5 | 300 | `AT+BEACON=004C,0001,27F1,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 126 | BK-01-126 | F1 | 0001 | 10226 | 27F2 | C5 | 300 | `AT+BEACON=004C,0001,27F2,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 127 | BK-01-127 | F1 | 0001 | 10227 | 27F3 | C5 | 300 | `AT+BEACON=004C,0001,27F3,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 128 | BK-01-128 | F1 | 0001 | 10228 | 27F4 | C5 | 300 | `AT+BEACON=004C,0001,27F4,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 129 | BK-01-129 | F1 | 0001 | 10229 | 27F5 | C5 | 300 | `AT+BEACON=004C,0001,27F5,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 130 | BK-01-130 | F1 | 0001 | 10230 | 27F6 | C5 | 300 | `AT+BEACON=004C,0001,27F6,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 131 | BK-01-131 | F1 | 0001 | 10231 | 27F7 | C5 | 300 | `AT+BEACON=004C,0001,27F7,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 132 | BK-01-132 | F1 | 0001 | 10232 | 27F8 | C5 | 300 | `AT+BEACON=004C,0001,27F8,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 133 | BK-01-133 | F1 | 0001 | 10233 | 27F9 | C5 | 300 | `AT+BEACON=004C,0001,27F9,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 134 | BK-01-134 | F1 | 0001 | 10234 | 27FA | C5 | 300 | `AT+BEACON=004C,0001,27FA,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 135 | BK-01-135 | F1 | 0001 | 10235 | 27FB | C5 | 300 | `AT+BEACON=004C,0001,27FB,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 136 | BK-01-136 | F1 | 0001 | 10236 | 27FC | C5 | 300 | `AT+BEACON=004C,0001,27FC,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 137 | BK-01-137 | F1 | 0001 | 10237 | 27FD | C5 | 300 | `AT+BEACON=004C,0001,27FD,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 138 | BK-01-138 | F1 | 0001 | 10238 | 27FE | C5 | 300 | `AT+BEACON=004C,0001,27FE,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 139 | BK-01-139 | F1 | 0001 | 10239 | 27FF | C5 | 300 | `AT+BEACON=004C,0001,27FF,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 140 | BK-01-140 | F1 | 0001 | 10240 | 2800 | C5 | 300 | `AT+BEACON=004C,0001,2800,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 141 | BK-01-141 | F1 | 0001 | 10241 | 2801 | C5 | 300 | `AT+BEACON=004C,0001,2801,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 142 | BK-01-142 | F1 | 0001 | 10242 | 2802 | C5 | 300 | `AT+BEACON=004C,0001,2802,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 143 | BK-01-143 | F1 | 0001 | 10243 | 2803 | C5 | 300 | `AT+BEACON=004C,0001,2803,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 144 | BK-01-144 | F1 | 0001 | 10244 | 2804 | C5 | 300 | `AT+BEACON=004C,0001,2804,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 145 | BK-01-145 | F1 | 0001 | 10245 | 2805 | C5 | 300 | `AT+BEACON=004C,0001,2805,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 146 | BK-01-146 | F1 | 0001 | 10246 | 2806 | C5 | 300 | `AT+BEACON=004C,0001,2806,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 147 | BK-01-147 | F1 | 0001 | 10247 | 2807 | C5 | 300 | `AT+BEACON=004C,0001,2807,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 148 | BK-01-148 | F1 | 0001 | 10248 | 2808 | C5 | 300 | `AT+BEACON=004C,0001,2808,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 149 | BK-01-149 | F1 | 0001 | 10249 | 2809 | C5 | 300 | `AT+BEACON=004C,0001,2809,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 150 | BK-01-150 | F1 | 0001 | 10250 | 280A | C5 | 300 | `AT+BEACON=004C,0001,280A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 151 | BK-01-151 | F1 | 0001 | 10251 | 280B | C5 | 300 | `AT+BEACON=004C,0001,280B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 152 | BK-01-152 | F1 | 0001 | 10252 | 280C | C5 | 300 | `AT+BEACON=004C,0001,280C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 153 | BK-01-153 | F1 | 0001 | 10253 | 280D | C5 | 300 | `AT+BEACON=004C,0001,280D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 154 | BK-01-154 | F1 | 0001 | 10254 | 280E | C5 | 300 | `AT+BEACON=004C,0001,280E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 155 | BK-01-155 | F1 | 0001 | 10255 | 280F | C5 | 300 | `AT+BEACON=004C,0001,280F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 156 | BK-02-001 | F2 | 0002 | 10256 | 2810 | C5 | 300 | `AT+BEACON=004C,0002,2810,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 157 | BK-02-002 | F2 | 0002 | 10257 | 2811 | C5 | 300 | `AT+BEACON=004C,0002,2811,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 158 | BK-02-003 | F2 | 0002 | 10258 | 2812 | C5 | 300 | `AT+BEACON=004C,0002,2812,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 159 | BK-02-004 | F2 | 0002 | 10259 | 2813 | C5 | 300 | `AT+BEACON=004C,0002,2813,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 160 | BK-02-005 | F2 | 0002 | 10260 | 2814 | C5 | 300 | `AT+BEACON=004C,0002,2814,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 161 | BK-02-006 | F2 | 0002 | 10261 | 2815 | C5 | 300 | `AT+BEACON=004C,0002,2815,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 162 | BK-02-007 | F2 | 0002 | 10262 | 2816 | C5 | 300 | `AT+BEACON=004C,0002,2816,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 163 | BK-02-008 | F2 | 0002 | 10263 | 2817 | C5 | 300 | `AT+BEACON=004C,0002,2817,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 164 | BK-02-009 | F2 | 0002 | 10264 | 2818 | C5 | 300 | `AT+BEACON=004C,0002,2818,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 165 | BK-02-010 | F2 | 0002 | 10265 | 2819 | C5 | 300 | `AT+BEACON=004C,0002,2819,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 166 | BK-02-011 | F2 | 0002 | 10266 | 281A | C5 | 300 | `AT+BEACON=004C,0002,281A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 167 | BK-02-012 | F2 | 0002 | 10267 | 281B | C5 | 300 | `AT+BEACON=004C,0002,281B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 168 | BK-02-013 | F2 | 0002 | 10268 | 281C | C5 | 300 | `AT+BEACON=004C,0002,281C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 169 | BK-02-014 | F2 | 0002 | 10269 | 281D | C5 | 300 | `AT+BEACON=004C,0002,281D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 170 | BK-02-015 | F2 | 0002 | 10270 | 281E | C5 | 300 | `AT+BEACON=004C,0002,281E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 171 | BK-02-016 | F2 | 0002 | 10271 | 281F | C5 | 300 | `AT+BEACON=004C,0002,281F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 172 | BK-02-017 | F2 | 0002 | 10272 | 2820 | C5 | 300 | `AT+BEACON=004C,0002,2820,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 173 | BK-02-018 | F2 | 0002 | 10273 | 2821 | C5 | 300 | `AT+BEACON=004C,0002,2821,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 174 | BK-02-019 | F2 | 0002 | 10274 | 2822 | C5 | 300 | `AT+BEACON=004C,0002,2822,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 175 | BK-02-020 | F2 | 0002 | 10275 | 2823 | C5 | 300 | `AT+BEACON=004C,0002,2823,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 176 | BK-02-021 | F2 | 0002 | 10276 | 2824 | C5 | 300 | `AT+BEACON=004C,0002,2824,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 177 | BK-02-022 | F2 | 0002 | 10277 | 2825 | C5 | 300 | `AT+BEACON=004C,0002,2825,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 178 | BK-02-023 | F2 | 0002 | 10278 | 2826 | C5 | 300 | `AT+BEACON=004C,0002,2826,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 179 | BK-02-024 | F2 | 0002 | 10279 | 2827 | C5 | 300 | `AT+BEACON=004C,0002,2827,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 180 | BK-02-025 | F2 | 0002 | 10280 | 2828 | C5 | 300 | `AT+BEACON=004C,0002,2828,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 181 | BK-02-026 | F2 | 0002 | 10281 | 2829 | C5 | 300 | `AT+BEACON=004C,0002,2829,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 182 | BK-02-027 | F2 | 0002 | 10282 | 282A | C5 | 300 | `AT+BEACON=004C,0002,282A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 183 | BK-02-028 | F2 | 0002 | 10283 | 282B | C5 | 300 | `AT+BEACON=004C,0002,282B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 184 | BK-02-029 | F2 | 0002 | 10284 | 282C | C5 | 300 | `AT+BEACON=004C,0002,282C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 185 | BK-02-030 | F2 | 0002 | 10285 | 282D | C5 | 300 | `AT+BEACON=004C,0002,282D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 186 | BK-02-031 | F2 | 0002 | 10286 | 282E | C5 | 300 | `AT+BEACON=004C,0002,282E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 187 | BK-02-032 | F2 | 0002 | 10287 | 282F | C5 | 300 | `AT+BEACON=004C,0002,282F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 188 | BK-02-033 | F2 | 0002 | 10288 | 2830 | C5 | 300 | `AT+BEACON=004C,0002,2830,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 189 | BK-02-034 | F2 | 0002 | 10289 | 2831 | C5 | 300 | `AT+BEACON=004C,0002,2831,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 190 | BK-02-035 | F2 | 0002 | 10290 | 2832 | C5 | 300 | `AT+BEACON=004C,0002,2832,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 191 | BK-02-036 | F2 | 0002 | 10291 | 2833 | C5 | 300 | `AT+BEACON=004C,0002,2833,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 192 | BK-02-037 | F2 | 0002 | 10292 | 2834 | C5 | 300 | `AT+BEACON=004C,0002,2834,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 193 | BK-02-038 | F2 | 0002 | 10293 | 2835 | C5 | 300 | `AT+BEACON=004C,0002,2835,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 194 | BK-02-039 | F2 | 0002 | 10294 | 2836 | C5 | 300 | `AT+BEACON=004C,0002,2836,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 195 | BK-02-040 | F2 | 0002 | 10295 | 2837 | C5 | 300 | `AT+BEACON=004C,0002,2837,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 196 | BK-02-041 | F2 | 0002 | 10296 | 2838 | C5 | 300 | `AT+BEACON=004C,0002,2838,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 197 | BK-02-042 | F2 | 0002 | 10297 | 2839 | C5 | 300 | `AT+BEACON=004C,0002,2839,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 198 | BK-02-043 | F2 | 0002 | 10298 | 283A | C5 | 300 | `AT+BEACON=004C,0002,283A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 199 | BK-02-044 | F2 | 0002 | 10299 | 283B | C5 | 300 | `AT+BEACON=004C,0002,283B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 200 | BK-02-045 | F2 | 0002 | 10300 | 283C | C5 | 300 | `AT+BEACON=004C,0002,283C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 201 | BK-02-046 | F2 | 0002 | 10301 | 283D | C5 | 300 | `AT+BEACON=004C,0002,283D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 202 | BK-02-047 | F2 | 0002 | 10302 | 283E | C5 | 300 | `AT+BEACON=004C,0002,283E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 203 | BK-02-048 | F2 | 0002 | 10303 | 283F | C5 | 300 | `AT+BEACON=004C,0002,283F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 204 | BK-02-049 | F2 | 0002 | 10304 | 2840 | C5 | 300 | `AT+BEACON=004C,0002,2840,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 205 | BK-02-050 | F2 | 0002 | 10305 | 2841 | C5 | 300 | `AT+BEACON=004C,0002,2841,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 206 | BK-02-051 | F2 | 0002 | 10306 | 2842 | C5 | 300 | `AT+BEACON=004C,0002,2842,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 207 | BK-02-052 | F2 | 0002 | 10307 | 2843 | C5 | 300 | `AT+BEACON=004C,0002,2843,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 208 | BK-02-053 | F2 | 0002 | 10308 | 2844 | C5 | 300 | `AT+BEACON=004C,0002,2844,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 209 | BK-02-054 | F2 | 0002 | 10309 | 2845 | C5 | 300 | `AT+BEACON=004C,0002,2845,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 210 | BK-02-055 | F2 | 0002 | 10310 | 2846 | C5 | 300 | `AT+BEACON=004C,0002,2846,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 211 | BK-02-056 | F2 | 0002 | 10311 | 2847 | C5 | 300 | `AT+BEACON=004C,0002,2847,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 212 | BK-02-057 | F2 | 0002 | 10312 | 2848 | C5 | 300 | `AT+BEACON=004C,0002,2848,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 213 | BK-02-058 | F2 | 0002 | 10313 | 2849 | C5 | 300 | `AT+BEACON=004C,0002,2849,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 214 | BK-02-059 | F2 | 0002 | 10314 | 284A | C5 | 300 | `AT+BEACON=004C,0002,284A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 215 | BK-02-060 | F2 | 0002 | 10315 | 284B | C5 | 300 | `AT+BEACON=004C,0002,284B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 216 | BK-02-061 | F2 | 0002 | 10316 | 284C | C5 | 300 | `AT+BEACON=004C,0002,284C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 217 | BK-02-062 | F2 | 0002 | 10317 | 284D | C5 | 300 | `AT+BEACON=004C,0002,284D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 218 | BK-02-063 | F2 | 0002 | 10318 | 284E | C5 | 300 | `AT+BEACON=004C,0002,284E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 219 | BK-02-064 | F2 | 0002 | 10319 | 284F | C5 | 300 | `AT+BEACON=004C,0002,284F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 220 | BK-02-065 | F2 | 0002 | 10320 | 2850 | C5 | 300 | `AT+BEACON=004C,0002,2850,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 221 | BK-02-066 | F2 | 0002 | 10321 | 2851 | C5 | 300 | `AT+BEACON=004C,0002,2851,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 222 | BK-02-067 | F2 | 0002 | 10322 | 2852 | C5 | 300 | `AT+BEACON=004C,0002,2852,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 223 | BK-02-068 | F2 | 0002 | 10323 | 2853 | C5 | 300 | `AT+BEACON=004C,0002,2853,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 224 | BK-02-069 | F2 | 0002 | 10324 | 2854 | C5 | 300 | `AT+BEACON=004C,0002,2854,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 225 | BK-02-070 | F2 | 0002 | 10325 | 2855 | C5 | 300 | `AT+BEACON=004C,0002,2855,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 226 | BK-02-071 | F2 | 0002 | 10326 | 2856 | C5 | 300 | `AT+BEACON=004C,0002,2856,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 227 | BK-02-072 | F2 | 0002 | 10327 | 2857 | C5 | 300 | `AT+BEACON=004C,0002,2857,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 228 | BK-02-073 | F2 | 0002 | 10328 | 2858 | C5 | 300 | `AT+BEACON=004C,0002,2858,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 229 | BK-02-074 | F2 | 0002 | 10329 | 2859 | C5 | 300 | `AT+BEACON=004C,0002,2859,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 230 | BK-02-075 | F2 | 0002 | 10330 | 285A | C5 | 300 | `AT+BEACON=004C,0002,285A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 231 | BK-02-076 | F2 | 0002 | 10331 | 285B | C5 | 300 | `AT+BEACON=004C,0002,285B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 232 | BK-02-077 | F2 | 0002 | 10332 | 285C | C5 | 300 | `AT+BEACON=004C,0002,285C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 233 | BK-02-078 | F2 | 0002 | 10333 | 285D | C5 | 300 | `AT+BEACON=004C,0002,285D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 234 | BK-02-079 | F2 | 0002 | 10334 | 285E | C5 | 300 | `AT+BEACON=004C,0002,285E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 235 | BK-02-080 | F2 | 0002 | 10335 | 285F | C5 | 300 | `AT+BEACON=004C,0002,285F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 236 | BK-02-081 | F2 | 0002 | 10336 | 2860 | C5 | 300 | `AT+BEACON=004C,0002,2860,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 237 | BK-02-082 | F2 | 0002 | 10337 | 2861 | C5 | 300 | `AT+BEACON=004C,0002,2861,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 238 | BK-02-083 | F2 | 0002 | 10338 | 2862 | C5 | 300 | `AT+BEACON=004C,0002,2862,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 239 | BK-02-084 | F2 | 0002 | 10339 | 2863 | C5 | 300 | `AT+BEACON=004C,0002,2863,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 240 | BK-02-085 | F2 | 0002 | 10340 | 2864 | C5 | 300 | `AT+BEACON=004C,0002,2864,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 241 | BK-02-086 | F2 | 0002 | 10341 | 2865 | C5 | 300 | `AT+BEACON=004C,0002,2865,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 242 | BK-02-087 | F2 | 0002 | 10342 | 2866 | C5 | 300 | `AT+BEACON=004C,0002,2866,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 243 | BK-02-088 | F2 | 0002 | 10343 | 2867 | C5 | 300 | `AT+BEACON=004C,0002,2867,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 244 | BK-02-089 | F2 | 0002 | 10344 | 2868 | C5 | 300 | `AT+BEACON=004C,0002,2868,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 245 | BK-02-090 | F2 | 0002 | 10345 | 2869 | C5 | 300 | `AT+BEACON=004C,0002,2869,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 246 | BK-02-091 | F2 | 0002 | 10346 | 286A | C5 | 300 | `AT+BEACON=004C,0002,286A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 247 | BK-02-092 | F2 | 0002 | 10347 | 286B | C5 | 300 | `AT+BEACON=004C,0002,286B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 248 | BK-02-093 | F2 | 0002 | 10348 | 286C | C5 | 300 | `AT+BEACON=004C,0002,286C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 249 | BK-02-094 | F2 | 0002 | 10349 | 286D | C5 | 300 | `AT+BEACON=004C,0002,286D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 250 | BK-02-095 | F2 | 0002 | 10350 | 286E | C5 | 300 | `AT+BEACON=004C,0002,286E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 251 | BK-02-096 | F2 | 0002 | 10351 | 286F | C5 | 300 | `AT+BEACON=004C,0002,286F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 252 | BK-02-097 | F2 | 0002 | 10352 | 2870 | C5 | 300 | `AT+BEACON=004C,0002,2870,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 253 | BK-02-098 | F2 | 0002 | 10353 | 2871 | C5 | 300 | `AT+BEACON=004C,0002,2871,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 254 | BK-02-099 | F2 | 0002 | 10354 | 2872 | C5 | 300 | `AT+BEACON=004C,0002,2872,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 255 | BK-02-100 | F2 | 0002 | 10355 | 2873 | C5 | 300 | `AT+BEACON=004C,0002,2873,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 256 | BK-02-101 | F2 | 0002 | 10356 | 2874 | C5 | 300 | `AT+BEACON=004C,0002,2874,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 257 | BK-02-102 | F2 | 0002 | 10357 | 2875 | C5 | 300 | `AT+BEACON=004C,0002,2875,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 258 | BK-02-103 | F2 | 0002 | 10358 | 2876 | C5 | 300 | `AT+BEACON=004C,0002,2876,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 259 | BK-02-104 | F2 | 0002 | 10359 | 2877 | C5 | 300 | `AT+BEACON=004C,0002,2877,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 260 | BK-02-105 | F2 | 0002 | 10360 | 2878 | C5 | 300 | `AT+BEACON=004C,0002,2878,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 261 | BK-02-106 | F2 | 0002 | 10361 | 2879 | C5 | 300 | `AT+BEACON=004C,0002,2879,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 262 | BK-02-107 | F2 | 0002 | 10362 | 287A | C5 | 300 | `AT+BEACON=004C,0002,287A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 263 | BK-02-108 | F2 | 0002 | 10363 | 287B | C5 | 300 | `AT+BEACON=004C,0002,287B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 264 | BK-02-109 | F2 | 0002 | 10364 | 287C | C5 | 300 | `AT+BEACON=004C,0002,287C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 265 | BK-T-F1-001 | F1 | 0001 | 10365 | 287D | C5 | 300 | `AT+BEACON=004C,0001,287D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 266 | BK-T-F1-002 | F1 | 0001 | 10366 | 287E | C5 | 300 | `AT+BEACON=004C,0001,287E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 267 | BK-T-F1-003 | F1 | 0001 | 10367 | 287F | C5 | 300 | `AT+BEACON=004C,0001,287F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 268 | BK-T-F1-004 | F1 | 0001 | 10368 | 2880 | C5 | 300 | `AT+BEACON=004C,0001,2880,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 269 | BK-T-F1-005 | F1 | 0001 | 10369 | 2881 | C5 | 300 | `AT+BEACON=004C,0001,2881,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 270 | BK-T-F1-006 | F1 | 0001 | 10370 | 2882 | C5 | 300 | `AT+BEACON=004C,0001,2882,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 271 | BK-T-F1-007 | F1 | 0001 | 10371 | 2883 | C5 | 300 | `AT+BEACON=004C,0001,2883,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 272 | BK-T-F1-008 | F1 | 0001 | 10372 | 2884 | C5 | 300 | `AT+BEACON=004C,0001,2884,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 273 | BK-T-F1-009 | F1 | 0001 | 10373 | 2885 | C5 | 300 | `AT+BEACON=004C,0001,2885,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 274 | BK-T-F1-010 | F1 | 0001 | 10374 | 2886 | C5 | 300 | `AT+BEACON=004C,0001,2886,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 275 | BK-T-F1-011 | F1 | 0001 | 10375 | 2887 | C5 | 300 | `AT+BEACON=004C,0001,2887,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 276 | BK-T-F1-012 | F1 | 0001 | 10376 | 2888 | C5 | 300 | `AT+BEACON=004C,0001,2888,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 277 | BK-T-F1-013 | F1 | 0001 | 10377 | 2889 | C5 | 300 | `AT+BEACON=004C,0001,2889,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 278 | BK-T-F1-014 | F1 | 0001 | 10378 | 288A | C5 | 300 | `AT+BEACON=004C,0001,288A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 279 | BK-T-F1-015 | F1 | 0001 | 10379 | 288B | C5 | 300 | `AT+BEACON=004C,0001,288B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 280 | BK-T-F1-016 | F1 | 0001 | 10380 | 288C | C5 | 300 | `AT+BEACON=004C,0001,288C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 281 | BK-T-F1-017 | F1 | 0001 | 10381 | 288D | C5 | 300 | `AT+BEACON=004C,0001,288D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 282 | BK-T-F1-018 | F1 | 0001 | 10382 | 288E | C5 | 300 | `AT+BEACON=004C,0001,288E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 283 | BK-T-F1-019 | F1 | 0001 | 10383 | 288F | C5 | 300 | `AT+BEACON=004C,0001,288F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 284 | BK-T-F1-020 | F1 | 0001 | 10384 | 2890 | C5 | 300 | `AT+BEACON=004C,0001,2890,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 285 | BK-T-F1-021 | F1 | 0001 | 10385 | 2891 | C5 | 300 | `AT+BEACON=004C,0001,2891,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 286 | BK-T-F1-022 | F1 | 0001 | 10386 | 2892 | C5 | 300 | `AT+BEACON=004C,0001,2892,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 287 | BK-T-F1-023 | F1 | 0001 | 10387 | 2893 | C5 | 300 | `AT+BEACON=004C,0001,2893,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 288 | BK-T-F1-024 | F1 | 0001 | 10388 | 2894 | C5 | 300 | `AT+BEACON=004C,0001,2894,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 289 | BK-T-F1-025 | F1 | 0001 | 10389 | 2895 | C5 | 300 | `AT+BEACON=004C,0001,2895,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 290 | BK-T-F1-026 | F1 | 0001 | 10390 | 2896 | C5 | 300 | `AT+BEACON=004C,0001,2896,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 291 | BK-T-F1-027 | F1 | 0001 | 10391 | 2897 | C5 | 300 | `AT+BEACON=004C,0001,2897,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 292 | BK-T-F1-028 | F1 | 0001 | 10392 | 2898 | C5 | 300 | `AT+BEACON=004C,0001,2898,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 293 | BK-T-F1-029 | F1 | 0001 | 10393 | 2899 | C5 | 300 | `AT+BEACON=004C,0001,2899,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 294 | BK-T-F1-030 | F1 | 0001 | 10394 | 289A | C5 | 300 | `AT+BEACON=004C,0001,289A,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 295 | BK-T-F1-031 | F1 | 0001 | 10395 | 289B | C5 | 300 | `AT+BEACON=004C,0001,289B,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 296 | BK-T-F1-032 | F1 | 0001 | 10396 | 289C | C5 | 300 | `AT+BEACON=004C,0001,289C,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 297 | BK-T-F1-033 | F1 | 0001 | 10397 | 289D | C5 | 300 | `AT+BEACON=004C,0001,289D,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 298 | BK-T-F1-034 | F1 | 0001 | 10398 | 289E | C5 | 300 | `AT+BEACON=004C,0001,289E,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 299 | BK-T-F1-035 | F1 | 0001 | 10399 | 289F | C5 | 300 | `AT+BEACON=004C,0001,289F,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 300 | BK-T-F1-036 | F1 | 0001 | 10400 | 28A0 | C5 | 300 | `AT+BEACON=004C,0001,28A0,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 301 | BK-T-F1-037 | F1 | 0001 | 10401 | 28A1 | C5 | 300 | `AT+BEACON=004C,0001,28A1,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 302 | BK-T-F1-038 | F1 | 0001 | 10402 | 28A2 | C5 | 300 | `AT+BEACON=004C,0001,28A2,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 303 | BK-T-F1-039 | F1 | 0001 | 10403 | 28A3 | C5 | 300 | `AT+BEACON=004C,0001,28A3,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 304 | BK-T-F1-040 | F1 | 0001 | 10404 | 28A4 | C5 | 300 | `AT+BEACON=004C,0001,28A4,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 305 | BK-T-F1-041 | F1 | 0001 | 10405 | 28A5 | C5 | 300 | `AT+BEACON=004C,0001,28A5,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 306 | BK-T-F1-042 | F1 | 0001 | 10406 | 28A6 | C5 | 300 | `AT+BEACON=004C,0001,28A6,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 307 | BK-T-F1-043 | F1 | 0001 | 10407 | 28A7 | C5 | 300 | `AT+BEACON=004C,0001,28A7,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 308 | BK-T-F1-044 | F1 | 0001 | 10408 | 28A8 | C5 | 300 | `AT+BEACON=004C,0001,28A8,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 309 | BK-T-F1-045 | F1 | 0001 | 10409 | 28A9 | C5 | 300 | `AT+BEACON=004C,0001,28A9,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 310 | BK-T-F1-046 | F1 | 0001 | 10410 | 28AA | C5 | 300 | `AT+BEACON=004C,0001,28AA,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 311 | BK-T-F1-047 | F1 | 0001 | 10411 | 28AB | C5 | 300 | `AT+BEACON=004C,0001,28AB,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 312 | BK-T-F1-048 | F1 | 0001 | 10412 | 28AC | C5 | 300 | `AT+BEACON=004C,0001,28AC,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 313 | BK-T-F1-049 | F1 | 0001 | 10413 | 28AD | C5 | 300 | `AT+BEACON=004C,0001,28AD,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 314 | BK-T-F2-001 | F2 | 0002 | 10414 | 28AE | C5 | 300 | `AT+BEACON=004C,0002,28AE,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 315 | BK-T-F2-002 | F2 | 0002 | 10415 | 28AF | C5 | 300 | `AT+BEACON=004C,0002,28AF,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 316 | BK-T-F2-003 | F2 | 0002 | 10416 | 28B0 | C5 | 300 | `AT+BEACON=004C,0002,28B0,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 317 | BK-T-F2-004 | F2 | 0002 | 10417 | 28B1 | C5 | 300 | `AT+BEACON=004C,0002,28B1,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 318 | BK-T-F2-005 | F2 | 0002 | 10418 | 28B2 | C5 | 300 | `AT+BEACON=004C,0002,28B2,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 319 | BK-T-F2-006 | F2 | 0002 | 10419 | 28B3 | C5 | 300 | `AT+BEACON=004C,0002,28B3,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 320 | BK-T-F2-007 | F2 | 0002 | 10420 | 28B4 | C5 | 300 | `AT+BEACON=004C,0002,28B4,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 321 | BK-T-F2-008 | F2 | 0002 | 10421 | 28B5 | C5 | 300 | `AT+BEACON=004C,0002,28B5,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 322 | BK-T-F2-009 | F2 | 0002 | 10422 | 28B6 | C5 | 300 | `AT+BEACON=004C,0002,28B6,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 323 | BK-T-F2-010 | F2 | 0002 | 10423 | 28B7 | C5 | 300 | `AT+BEACON=004C,0002,28B7,C5,B9407F30F5F8466EAFF925556B57FE6D` |
| 324 | BK-T-F2-011 | F2 | 0002 | 10424 | 28B8 | C5 | 300 | `AT+BEACON=004C,0002,28B8,C5,B9407F30F5F8466EAFF925556B57FE6D` |

## 三、RSSI@1m 现场校准（重要）

指纹定位以原始 RSSI 指纹库为主，RSSI@1m 仅影响帧内距离估计，因此**不校准也能用**；
但建议抽测校准以提升任何基于帧距离的逻辑精度。步骤：
1. 信标固定在 -10 dBm 后，手机置于其正下方 **1 m** 处，用 nRF Connect 读取该信标的 RSSI（如读到 -57 dBm）。
2. 将读到的负值转为补码 hex：字节 = (256 + RSSI) & 0xFF。例：-57 -> 0xC7；-62 -> 0xC2；-59 -> 0xC5。
3. 把对应信标 `AT+BEACON` 命令中的第 4 参数替换为该值（保持其余不变），重发即可。
4. 若全楼统一一个典型值，可直接批量替换清单中所有的 `C5`。

> 本清单由 `src/tools/gen_beacon_at_commands.py` 依据部署计划 JSON 生成，部署计划更新后重新运行即可。