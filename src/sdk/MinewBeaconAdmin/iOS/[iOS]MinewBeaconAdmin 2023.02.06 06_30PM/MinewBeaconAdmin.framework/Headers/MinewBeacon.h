//
//  MinewBeacon.h
//  BeaconCFG
//
//  Created by SACRELEE on 18/09/2016.
//  Copyright © 2016 YLWL. All rights reserved.
//

#import <Foundation/Foundation.h>

@interface MinewBeacon : NSObject

// uuid
@property (nonatomic, copy, readonly ) NSString *uuid;

// major
@property (nonatomic, assign, readonly ) NSInteger major;

// minor
@property (nonatomic, assign, readonly ) NSInteger minor;

// name
@property (nonatomic, copy, readonly ) NSString *name;

// deviceId
@property (nonatomic, assign, readonly ) NSInteger deviceId;

// mac
@property (nonatomic, copy, readonly) NSString *mac;

// txpower
@property (nonatomic, assign, readonly ) NSInteger txPower;

// rssi
@property (nonatomic, assign, readonly ) NSInteger rssi;

// battery
@property (nonatomic, assign, readonly ) NSInteger battery;

// in range or not.
@property (nonatomic, assign, readonly ) BOOL inRange;

// can be connected or not.
@property (nonatomic, assign, readonly ) BOOL connectable;

// voltage（Only the Info frame has a voltage attribute, and when the voltage value is greater than or equal to 0, it is a valid value.）
@property (nonatomic, assign, readonly ) NSInteger voltage;


// 导出json字串
- (NSString *)exportJSON;

// 导入json字串
- (void)importJSON:(NSString *)jsonString;


@end
