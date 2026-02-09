# ASSFun

自动化提取 ASS 字幕字体，并可实现字体子集化并进行视频混流。  
字幕请保持 Aegisub 的标准输出格式，字幕中也请不要乱用标签和括号(例如\fn和\p交替连续使用或者输入错误的大括号)，不然可能会出各种问题。  
基础使用说明: [ASSFun wiki](https://github.com/KyokuSai/ASSFont/wiki)  
如果您想使用字幕处理功能，请务必看一下字幕生成说明: [ASSFun wiki ‐ 字幕生成](https://github.com/KyokuSai/ASSFont/wiki/ASSFun-%E2%80%90-%E8%A7%86%E9%A2%91%E6%B7%B7%E6%B5%81)

# 关于

混流需要用到 mkvmerge.exe ，下载: https://mkvtoolnix.download/downloads.html 后安装，在同目录下就有。  
混流还需要用到 Mediainfo ，前往 https://mediaarea.net/zh-CN/MediaInfo 下载安装即可。
繁化使用繁化姬: https://zhconvert.org/  
卡拉OK模板化需要用到 aegisub-cli ，下载: https://github.com/Myaamori/aegisub-cli/releases 后将 exe 放在您使用的 Aegisub 同目录下即可。

# 更新日志

260210: 完善混流的逻辑。  
250711: 进行了一些小修改。  
250218: 修复了一个比较严重的子集化bug，将字幕格式更新到我们当前使用的格式。  
240731: 增加了样式表的选择。  
240702: 添加对ttc字体的支持，并修复了一些字体名称获取不全的问题；增加了额外添加英语字幕的功能，增加了对指定样式的保留；改用线程执行，界面不再会被卡住。  
240513: 为 aegisub-cli.exe 额外准备了文件处理，以避免部分文件名不能被其读取的问题。  
240512: 添加字幕生成功能；修复了一点文件逻辑问题。  
240510: 初版。
