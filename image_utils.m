#import <AVFoundation/AVFoundation.h>
#import <CoreMedia/CoreMedia.h>
#import <objc/runtime.h>
#import <UIKit/UIKit.h>

// ============================================================
//  vcam-ios 增强版：支持从 OBS 实时拉帧替换 iPhone 摄像头
// ------------------------------------------------------------
//  画面来源优先级：
//    1) 网络模式（OBS 直播帧，最高优先）：定时从 PC 端
//       obs_stream_server.py 提供的 HTTP 接口拉取最新 JPEG 帧，
//       实时覆盖摄像头画面。适用于 Safari/相机/微信等所有 App。
//    2) 本地模式（回退）：读取本地静态图片或视频文件。
//
//  Endereço do Mac usado nesta demonstração. Atualize e recompile se o IP mudar.
// ============================================================

// 本地替换媒体（静态图片或视频文件）—— 仅当网络帧不可用时作为回退
static NSString *const kReplacementMediaPath = @"/tmp/test.png";

static NSString *const kFrameURL = @"http://192.168.1.10:8080/frame.jpg";

// 网络轮询间隔（秒）—— 约 12 帧/秒，与中继的默认输出同步
static const NSTimeInterval kNetworkPollInterval = 0.08;

// 单帧下载超时（秒）
static const NSTimeInterval kNetworkFetchTimeout = 4.0;

typedef enum {
    VCamModeNone = 0,
    VCamModeImage,
    VCamModeVideo,
    VCamModeNetwork
} VCamMode;

static VCamMode currentMode = VCamModeNone;
static CGImageRef replacementImage = NULL;
static NSMutableArray *videoFrames = NULL;
static NSUInteger currentFrameIndex = 0;
static CIContext *sharedCIContext = NULL;
static NSObject *vcamLock = nil;

// ---- 网络模式内部状态 ----
static NSLock *networkLock = nil;
static CGImageRef networkImage = NULL;
static BOOL networkThreadStarted = NO;
static NSURLSession *networkSession = nil;

static NSString *effectiveFrameURL(void) {
    return kFrameURL;
}

// 带超时地从 OBS 中继服务器下载一帧 JPEG（阻塞当前轮询线程）
static NSData *downloadFrame(NSURL *url) {
    __block NSData *result = nil;
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:url];
    req.timeoutInterval = kNetworkFetchTimeout;
    req.cachePolicy = NSURLRequestReloadIgnoringLocalCacheData;

    NSURLSession *session = networkSession;
    if (!session) {
        NSURLSessionConfiguration *cfg = [NSURLSessionConfiguration defaultSessionConfiguration];
        cfg.timeoutIntervalForRequest = kNetworkFetchTimeout;
        cfg.timeoutIntervalForResource = kNetworkFetchTimeout;
        cfg.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
        session = [NSURLSession sessionWithConfiguration:cfg];
        networkSession = session;
    }

    [[session dataTaskWithRequest:req
                completionHandler:^(NSData *data, NSURLResponse *resp, NSError *err) {
        if (!err && data.length > 0) {
            result = data;
        }
        dispatch_semaphore_signal(sem);
    }] resume];

    dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW,
                                               (int64_t)((kNetworkFetchTimeout + 1.0) * NSEC_PER_SEC)));
    return result;
}

// 后台轮询线程：持续拉取 OBS 最新帧并刷新 networkImage
static void networkPollLoop(void) {
    @autoreleasepool {
        while (YES) {
            @autoreleasepool {
                NSString *urlString = effectiveFrameURL();
                NSURL *url = (urlString.length > 0) ? [NSURL URLWithString:urlString] : nil;
                NSData *data = url ? downloadFrame(url) : nil;
                UIImage *img = data ? [UIImage imageWithData:data] : nil;
                CGImageRef newRef = img.CGImage ? CGImageRetain(img.CGImage) : NULL;
                [networkLock lock];
                CGImageRef oldRef = networkImage;
                networkImage = newRef;
                [networkLock unlock];
                if (oldRef) {
                    CGImageRelease(oldRef);
                }
                [NSThread sleepForTimeInterval:kNetworkPollInterval];
            }
        }
    }
}

// 启动网络拉帧线程（幂等）
static void startNetworkPolling(void) {
    if (networkThreadStarted) {
        return;
    }
    if (!networkLock) {
        networkLock = [[NSLock alloc] init];
    }
    networkThreadStarted = YES;
    dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{
        networkPollLoop();
    });
}

void loadReplacementMedia(void) {
    if (!vcamLock) {
        vcamLock = [[NSObject alloc] init];
    }

    // 始终启动网络拉帧（OBS 实时画面优先）
    startNetworkPolling();

    if (![[NSFileManager defaultManager] fileExistsAtPath:kReplacementMediaPath]) {
        return;
    }

    NSString *extension = [[kReplacementMediaPath pathExtension] lowercaseString];
    if ([extension isEqualToString:@"png"] || [extension isEqualToString:@"jpg"] || [extension isEqualToString:@"jpeg"]) {
        UIImage *image = [UIImage imageWithContentsOfFile:kReplacementMediaPath];
        if (image && image.CGImage) {
            replacementImage = CGImageRetain(image.CGImage);
            currentMode = VCamModeImage;
        }
    }
    else if ([extension isEqualToString:@"mp4"] || [extension isEqualToString:@"mov"]) {
        NSURL *videoURL = [NSURL fileURLWithPath:kReplacementMediaPath];
        AVAsset *asset = [AVAsset assetWithURL:videoURL];

        NSError *error = nil;
        AVAssetReader *assetReader = [[AVAssetReader alloc] initWithAsset:asset error:&error];
        if (error) {
            return;
        }

        NSArray *videoTracks = [asset tracksWithMediaType:AVMediaTypeVideo];
        if (videoTracks.count == 0) {
            return;
        }

        AVAssetTrack *videoTrack = videoTracks[0];
        NSDictionary *outputSettings = @{ (NSString *)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_32BGRA) };
        AVAssetReaderTrackOutput *videoOutput = [[AVAssetReaderTrackOutput alloc] initWithTrack:videoTrack outputSettings:outputSettings];
        videoOutput.alwaysCopiesSampleData = NO;

        if (![assetReader canAddOutput:videoOutput]) {
            return;
        }

        [assetReader addOutput:videoOutput];
        [assetReader startReading];

        videoFrames = [[NSMutableArray alloc] init];
        int frameCount = 0;
        int maxFrames = 60;
        while (assetReader.status == AVAssetReaderStatusReading && frameCount < maxFrames) {
            CMSampleBufferRef sampleBuffer = [videoOutput copyNextSampleBuffer];
            if (sampleBuffer == NULL) {
                break;
            }

            CVPixelBufferRef pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer);
            if (pixelBuffer) {
                CVPixelBufferRetain(pixelBuffer);
                [videoFrames addObject:(__bridge id)pixelBuffer];
                frameCount++;
            }

            CFRelease(sampleBuffer);
        }

        if (frameCount > 0) {
            currentMode = VCamModeVideo;
        }

        [assetReader cancelReading];
    }
}

void drawReplacementOntoBuffer(CVPixelBufferRef targetBuffer) {
    @synchronized(vcamLock) {
        CIImage *replacementCIImage = nil;

        // 1) 网络帧优先：取最新一帧 OBS 画面
        CGImageRef netImg = NULL;
        [networkLock lock];
        if (networkImage) {
            netImg = CGImageRetain(networkImage);
        }
        [networkLock unlock];

        if (netImg) {
            replacementCIImage = [CIImage imageWithCGImage:netImg];
            CGImageRelease(netImg);
        }
        // 2) 本地静态图片回退
        else if (currentMode == VCamModeImage) {
            replacementCIImage = [CIImage imageWithCGImage:replacementImage];
        }
        // 3) 本地视频回退
        else if (currentMode == VCamModeVideo) {
            CVPixelBufferRef videoFrame = (__bridge CVPixelBufferRef)videoFrames[currentFrameIndex];
            currentFrameIndex = (currentFrameIndex + 1) % videoFrames.count;
            replacementCIImage = [CIImage imageWithCVPixelBuffer:videoFrame];
        }

        if (!replacementCIImage) {
            return;
        }

        if (sharedCIContext == NULL) {
            sharedCIContext = [CIContext context];
        }

        CGFloat targetWidth = CVPixelBufferGetWidth(targetBuffer);
        CGFloat targetHeight = CVPixelBufferGetHeight(targetBuffer);
        CGRect replacementExtent = replacementCIImage.extent;

        CGFloat scaleX = targetWidth / replacementExtent.size.width;
        CGFloat scaleY = targetHeight / replacementExtent.size.height;
        // Preenche o quadro inteiro: a imagem horizontal do OBS é cortada
        // nas laterais ao ser usada como câmera vertical.
        CGFloat scale = MAX(scaleX, scaleY);

        CGAffineTransform transform = CGAffineTransformMakeScale(scale, scale);
        CIImage *scaledImage = [replacementCIImage imageByApplyingTransform:transform];

        CGRect scaledExtent = scaledImage.extent;
        CGFloat offsetX = (targetWidth - scaledExtent.size.width) / 2.0;
        CGFloat offsetY = (targetHeight - scaledExtent.size.height) / 2.0;
        CGAffineTransform translationTransform = CGAffineTransformMakeTranslation(offsetX, offsetY);
        CIImage *finalImage = [[scaledImage imageByApplyingTransform:translationTransform]
            imageByCroppingToRect:CGRectMake(0, 0, targetWidth, targetHeight)];

        if (sharedCIContext) {
            [sharedCIContext render:finalImage toCVPixelBuffer:targetBuffer];
        }
    }
}
