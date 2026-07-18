"""Build the Q1 reel per user spec: trim 00:09-02:24 of the source clip,
new title, question-card intro, Roman-Urdu captions, and a like/subscribe outro.
"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__))
import overlays as ro
from PIL import Image

S = os.path.dirname(__file__)
BODY_FULL = f"{S}/q1_body.mp4"     # full processed (blurred-fit) body
CARD_PNG = f"{S}/question_card.png"
LT_PNG = f"{S}/lower_third.png"
OUTRO_PNG = f"{S}/outro.png"
TRANSPARENT = f"{S}/transparent.png"
CAP_DIR = f"{S}/caps_reel"
CARD_DUR, OUTRO_DUR = 3.6, 4.5
START, END = 9.0, 144.0            # user-specified window (clip/body time)
TITLE = "Sr. Software Engineer | Mentor"

# Authored Roman-Urdu captions (clip time). Fills over the unclear region are
# corroborated across both the medium and small transcription runs.
CAPS = [
 (9.5,14.1,"Woh poochte hain kya main Pakistan mein koi startup ya tech company",["startup","tech","company"]),
 (14.1,16.5,"start karne ka plan rakhta hoon.",["plan"]),
 (19.1,21.9,"Mera zaati taur par aisa plan nahin hai.",["plan"]),
 (21.9,24.3,"Ho sakta hai main koi hotel khol loon,",["hotel"]),
 (24.3,27.1,"khane ka restaurant.",["restaurant"]),
 (27.1,34.3,"Ya shayad Pakistan se bahar bhi.",[]),
 (34.3,40.7,"Personally mujhe food se relevant business karna hai.",["food","business"]),
 (40.7,44.0,"Halanke meri apni expertise nahin hai,",["expertise"]),
 (44.0,48.3,"main zyada se zyada achhi chai bana leta hoon.",[]),
 (48.3,50.9,"Khana main nahin banata.",[]),
 (50.9,54.2,"Kabhi majboori ho to kuch kar lete hain.",[]),
 (54.2,57.9,"Personally shayad main restaurant ki taraf jaaun,",["restaurant"]),
 (57.9,61.9,"lekin tech company/startup ka mujhe plan nahin hai.",["tech","company","startup","plan"]),
 (62.5,67.0,"Mere nazdeek yeh kaam tab acha hota hai",[]),
 (67.0,72.3,"jab aap ke paas acha idea ho.",["idea"]),
 (73.0,79.0,"Do tarah ke scenarios hote hain.",[]),
 (79.0,86.0,"Ek — aap globally duniya ko acche products/services de rahe hain.",["globally","products","services"]),
 (90.0,98.5,"Ya koi real problem solve karke acha business banayein.",["real","problem","solve","business"]),
 (100.0,106.0,"Uske liye achhi investment bhi chahiye.",["investment"]),
 (106.5,113.0,"Aur agar ek team apne saath build karein,",["team"]),
 (113.0,118.5,"to ek acha person hire karein.",["hire"]),
 (118.5,123.5,"Main kisi ko manipulate na karun,",[]),
 (123.5,129.0,"kisi developer ko 40-50 hazar pe rakh kar",["developer"]),
 (129.0,134.0,"us se bohot saara kaam na nikalwaun.",[]),
 (134.0,139.0,"Main manipulate nahin karna chahta,",[]),
 (139.0,143.8,"personally mera aisa plan nahin hai.",["plan"]),
]

def run(c): subprocess.run(c, check=True)
def dur(p): return float(subprocess.check_output(
    ["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",p]).decode())

def main():
    os.makedirs(CAP_DIR, exist_ok=True)
    Image.new("RGBA",(ro.W,ro.H),(0,0,0,0)).save(TRANSPARENT)
    ro.make_question_card("Sir app ka in future Pakistan mai koi tech company/startup start karna ka plan rakhta ha?","@M.Danish-y8q",CARD_PNG)
    ro.make_lower_third("Rao Waqas Akram", TITLE, LT_PNG)
    ro.make_outro(OUTRO_PNG)

    # 1) trim body to [START,END]
    body = f"{S}/reel_body.mp4"
    run(["ffmpeg","-y","-ss",str(START),"-i",BODY_FULL,"-t",str(END-START),
         "-c:v","libx264","-crf","19","-preset","medium","-pix_fmt","yuv420p",
         "-c:a","aac","-b:a","192k",body,"-loglevel","error"])
    bdur = dur(body)

    # 2) captions -> body time
    caps=[]
    for cs,ce,txt,hl in CAPS:
        s,e=max(cs,START)-START, min(ce,END)-START
        if e-s>0.15: caps.append((round(s,2),round(e,2),txt,hl))
    for i,(s,e,txt,hl) in enumerate(caps):
        ro.make_caption(txt, hl, f"{CAP_DIR}/cap_{i:03d}.png")

    # 3) caption layer (qtrle)
    lines=[]; t=0.0
    def add(img,d):
        if d>0.02: lines.append(f"file '{img}'"); lines.append(f"duration {d:.3f}")
    for i,(s,e,txt,hl) in enumerate(caps):
        s=max(s,t)
        if s>t: add(TRANSPARENT,s-t)
        add(f"{CAP_DIR}/cap_{i:03d}.png",max(0.1,e-s)); t=e
    if t<bdur: add(TRANSPARENT,bdur-t)
    lines.append(f"file '{TRANSPARENT}'")
    open(f"{S}/concat_reel.txt","w").write("\n".join(lines))
    cap_layer=f"{S}/caption_layer_reel.mov"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",f"{S}/concat_reel.txt","-r","30",
         "-vf","scale=1080:1920,format=rgba","-c:v","qtrle",cap_layer,"-loglevel","error"])

    # 4) composite captions + name tag (first 5s)
    body_final=f"{S}/reel_body_final.mp4"
    # shortest=1 + explicit -t so a longer caption layer can never extend/freeze
    # the body past its own audio (fixes the frozen-tail-with-no-audio bug).
    run(["ffmpeg","-y","-i",body,"-i",cap_layer,"-loop","1","-framerate","5","-t",str(bdur),"-i",LT_PNG,
         "-filter_complex","[0:v][1:v]overlay=0:0:shortest=1[v1];[v1][2:v]overlay=0:0:enable='between(t,0.2,5)'[v]",
         "-map","[v]","-map","0:a","-t",str(bdur),"-c:v","libx264","-crf","19","-preset","medium",
         "-pix_fmt","yuv420p","-c:a","aac","-b:a","192k",body_final,"-loglevel","error"])

    # 5) card + outro videos
    def still(png,d,out):
        run(["ffmpeg","-y","-loop","1","-t",str(d),"-i",png,"-f","lavfi","-t",str(d),
             "-i","anullsrc=channel_layout=stereo:sample_rate=48000","-vf","scale=1080:1920,format=yuv420p",
             "-c:v","libx264","-crf","19","-preset","medium","-c:a","aac","-b:a","192k","-shortest",out,"-loglevel","error"])
    card=f"{S}/card.mp4"; outro=f"{S}/outro.mp4"
    still(CARD_PNG,CARD_DUR,card)
    import outro; outro.main()   # animated outro.mp4 (logos fly in)

    # 6) concat card + body + outro
    out=f"{S}/Q1_reel_final.mp4"
    run(["ffmpeg","-y","-i",card,"-i",body_final,"-i",outro,"-filter_complex",
         "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]","-map","[v]","-map","[a]",
         "-c:v","libx264","-crf","19","-preset","medium","-pix_fmt","yuv420p",
         "-c:a","aac","-b:a","192k","-movflags","+faststart",out,"-loglevel","error"])
    print("REEL:", out, f"{dur(out):.1f}s ({len(caps)} captions)")

if __name__=="__main__":
    main()
