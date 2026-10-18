"""科学轨迹与功率流的开发MP4样片；不是最终三分钟比赛视频。"""
from __future__ import annotations

import importlib.metadata
import re
import subprocess

import numpy as np
import pandas as pd

from .io import PROJECT_ROOT,read_json,sha256,write_json
from .workflow import setup_figures


def render_development_video() -> dict:
    try:
        import imageio_ffmpeg
    except ImportError as error:
        raise RuntimeError("请先运行 python -m pip install -r requirements-video.txt") from error
    import matplotlib
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter
    setup_figures()
    encoder=imageio_ffmpeg.get_ffmpeg_exe()
    matplotlib.rcParams["animation.ffmpeg_path"]=encoder
    baseline=pd.read_csv(PROJECT_ROOT/"results/timeseries/control_25C_baseline.csv")
    optimized=pd.read_csv(PROJECT_ROOT/"results/timeseries/control_25C_optimized.csv")
    output=PROJECT_ROOT/"results/videos/C题_控制对照_开发样片.mp4"
    output.parent.mkdir(parents=True,exist_ok=True)
    preview=output.with_suffix(".png")
    fps,frames=12,180
    indices=np.linspace(0,len(optimized)-1,frames).astype(int)
    eb=np.cumsum(baseline.battery_w*baseline.dt_s)/3.6e6
    eo=np.cumsum(optimized.battery_w*optimized.dt_s)/3.6e6
    fig,axes=plt.subplots(2,2,figsize=(12.8,7.2),dpi=100)
    fig.subplots_adjust(left=.08,right=.98,bottom=.12,top=.84,hspace=.40,wspace=.26)
    fig.suptitle("C题开发样片｜25℃ CLTC 控制对照｜仿真结果",fontsize=17,y=.98)
    clock_text=fig.text(.5,.91,"",ha="center",fontsize=12)
    fig.text(.5,.035,"同工况、初始SOC94%、座舱24℃；曲线为净端电能。终态修正、模型限制及失败检验见阶段三报告。",ha="center",fontsize=10)
    labels=["牵引输入","回收回流","座舱空调","座舱加热","电池冷却","低压附件"]
    bars=axes[0,0].barh(labels,np.zeros(6),color=["#d47d34","#24846f","#3a87b7","#c56559","#65aeb5","#999999"])
    axes[0,0].set_xlim(-55,55)
    axes[0,0].set_xlabel("优化策略当前DC功率 / kW（回流为负）")
    axes[0,0].axvline(0,color="grey",lw=.8)
    energy_lines=[]
    soc_lines=[]
    for label,color in [("反馈基准","#53789b"),("优化规则","#31946f")]:
        energy_lines.append(axes[0,1].plot([],[],label=label,color=color)[0])
        soc_lines.append(axes[1,0].plot([],[],label=label,color=color)[0])
    axes[0,1].set(xlim=(0,1800),ylim=(0,max(float(eb.max()),float(eo.max()))*1.08),xlabel="时间 / s",ylabel="累计净端电能 / kWh")
    axes[1,0].set(xlim=(0,1800),ylim=(min(baseline.soc.min(),optimized.soc.min())*100-.3,94.3),xlabel="时间 / s",ylabel="SOC / %")
    temp_lines=[axes[1,1].plot([],[],label="优化电池",color="#d9903f")[0],axes[1,1].plot([],[],label="优化座舱",color="#279a85")[0]]
    axes[1,1].set(xlim=(0,1800),ylim=(21.5,26.5),xlabel="时间 / s",ylabel="温度 / ℃")
    axes[1,1].axhline(22,color="grey",ls="--",lw=.6)
    axes[1,1].axhline(26,color="grey",ls="--",lw=.6)
    for ax in [axes[0,1],axes[1,0],axes[1,1]]:
        ax.legend(loc="upper left",fontsize=9)
        ax.grid(alpha=.25)
    writer=FFMpegWriter(fps=fps,codec="libx264",bitrate=1800,metadata={"title":"C problem development demonstration", "comment":"Simulation only; not final contest submission"},
                       extra_args=["-pix_fmt","yuv420p","-movflags","+faststart"])
    with writer.saving(fig,str(output),dpi=100):
        for frame,index in enumerate(indices):
            row=optimized.iloc[index]
            powers=[max(row.motor_dc_w,0),min(row.motor_dc_w,0),row.ac_w,row.heater_w,row.cooler_w,row.low_voltage_w]
            for bar,value in zip(bars,powers):
                bar.set_width(value/1000)
            stop=index+1
            energy_lines[0].set_data(baseline.time_s.iloc[:stop],eb.iloc[:stop])
            energy_lines[1].set_data(optimized.time_s.iloc[:stop],eo.iloc[:stop])
            soc_lines[0].set_data(baseline.time_s.iloc[:stop],baseline.soc.iloc[:stop]*100)
            soc_lines[1].set_data(optimized.time_s.iloc[:stop],optimized.soc.iloc[:stop]*100)
            temp_lines[0].set_data(optimized.time_s.iloc[:stop],optimized.battery_c.iloc[:stop])
            temp_lines[1].set_data(optimized.time_s.iloc[:stop],optimized.cabin_c.iloc[:stop])
            clock_text.set_text(f"行程 {row.time_s:.0f} / 1800s     车速 {row.speed_m_s*3.6:.1f}km/h     电池端净功率 {row.battery_w/1000:+.1f}kW")
            writer.grab_frame()
            if (frame+1)%60==0:
                print(f"Development MP4: {frame+1}/{frames} frames",flush=True)
    plt.close(fig)
    probe=subprocess.run([encoder,"-hide_banner","-i",str(output)],capture_output=True,text=True,encoding="utf-8",errors="replace")
    match=re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)",probe.stderr)
    dimensions=re.search(r"Video:.*?(\d{3,5})x(\d{3,5})",probe.stderr)
    if match is None or dimensions is None:
        raise RuntimeError("无法读取生成MP4的时长/尺寸")
    duration=int(match[1])*3600+int(match[2])*60+float(match[3])
    width,height=map(int,dimensions.groups())
    decode=subprocess.run([encoder,"-v","error","-i",str(output),"-f","null","-"],capture_output=True,text=True)
    if decode.returncode!=0 or width!=1280 or height!=720 or abs(duration-frames/fps)>.1:
        raise RuntimeError("MP4尺寸、时长或完整解码核查失败")
    subprocess.run([encoder,"-v","error","-y","-ss","7.5","-i",str(output),"-frames:v","1",str(preview)],check=True,capture_output=True)
    metadata={"type":"development_sample_not_final_submission","path":output.relative_to(PROJECT_ROOT).as_posix(),
              "duration_s":duration,"width":width,"height":height,"fps":fps,"frames":frames,
              "bytes":output.stat().st_size,"sha256":sha256(output),"full_decode_passed":True,"audio":False,
              "imageio_ffmpeg_version":importlib.metadata.version("imageio-ffmpeg"),
              "control_freeze_sha256":sha256(PROJECT_ROOT/"records/control_freeze.json"),
              "sources":["results/timeseries/control_25C_baseline.csv","results/timeseries/control_25C_optimized.csv"],
              "limits":"No narration and no complete four-part contest narrative. Demonstrates dynamic data and MP4 encoding only."}
    write_json(PROJECT_ROOT/"records/development_video.json",metadata)
    state=read_json(PROJECT_ROOT/"records/latest_state.json")
    state["development_video"]=metadata
    write_json(PROJECT_ROOT/"records/latest_state.json",state)
    from .progress import write_manifest
    write_manifest(stage="stage3_control_with_development_MP4")
    return metadata
