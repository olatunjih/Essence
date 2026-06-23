"""Vision pipeline: classify / detect / OCR / segment."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# VISION PIPELINE  (classify / detect / OCR / segment, tiered T0→T3)
# ══════════════════════════════════════════════════════════════════════════════

def _tool_vision_task(path: str, task: str,
                       model: str = "auto",
                       hw: "HardwareProfile | None" = None) -> str:
    """
    Structured computer vision tasks beyond VLM Q&A.
    task: classify | detect | ocr | segment | face_verify
    Routes to the best available library for the hardware tier.
    T0: tesseract OCR only.
    T1: YOLOv8-nano + tesseract + torchvision ResNet classify.
    T2: YOLOv8-medium + PaddleOCR + ViT-B/16.
    T3: YOLOv8-x + SAM/DINO + LayoutLM document parsing.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return f"[vision] File not found: {path}"
    tier = hw.tier if hw else 1

    if task == "ocr":
        # PaddleOCR (T2+) → pytesseract (T0+)
        if tier >= 2:
            try:
                from paddleocr import PaddleOCR  # type: ignore
                ocr    = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
                result = ocr.ocr(str(p), cls=True)
                texts  = [line[1][0] for block in result for line in block]
                return json.dumps({"engine": "paddleocr", "text": "\n".join(texts)})
            except ImportError:
                pass
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            text = pytesseract.image_to_string(Image.open(p))
            return json.dumps({"engine": "tesseract", "text": text.strip()})
        except ImportError:
            return "[vision/ocr] Install: pip install pytesseract Pillow (and tesseract binary)"

    if task == "classify":
        if tier >= 2:
            try:
                from transformers import pipeline as hf_pipeline  # type: ignore
                m_id = (model if model != "auto"
                        else "google/vit-base-patch16-224")
                pipe = hf_pipeline("image-classification", model=m_id)
                from PIL import Image  # type: ignore
                res  = pipe(Image.open(p))
                return json.dumps({"model": m_id, "predictions": res[:5]})
            except ImportError:
                pass
        try:
            import torchvision.transforms as T   # type: ignore
            import torchvision.models as tvm     # type: ignore
            import torch                         # type: ignore
            from PIL import Image                # type: ignore
            img   = T.Compose([T.Resize(256), T.CenterCrop(224),
                                T.ToTensor(),
                                T.Normalize([0.485,0.456,0.406],
                                            [0.229,0.224,0.225])])(Image.open(p).convert("RGB"))
            net   = tvm.resnet50(weights="DEFAULT").eval()
            with torch.no_grad():
                logits = net(img.unsqueeze(0))
            top5  = torch.topk(logits.softmax(dim=1), 5)
            return json.dumps({"model": "resnet50",
                               "top5_scores": top5.values.tolist()[0],
                               "top5_indices": top5.indices.tolist()[0]})
        except ImportError:
            return "[vision/classify] Install: pip install torch torchvision Pillow"

    if task == "detect":
        try:
            from ultralytics import YOLO  # type: ignore
            yolo_model = ("yolov8n.pt" if tier <= 1
                          else "yolov8m.pt" if tier == 2 else "yolov8x.pt")
            if model != "auto": yolo_model = model
            net     = YOLO(yolo_model)
            results = net(str(p))[0]
            boxes   = []
            for box in results.boxes:
                boxes.append({
                    "class": results.names[int(box.cls)],
                    "confidence": round(float(box.conf), 3),
                    "bbox": [round(float(x), 1) for x in box.xyxy[0].tolist()],
                })
            return json.dumps({"model": yolo_model,
                               "detections": boxes, "count": len(boxes)})
        except ImportError:
            return "[vision/detect] Install: pip install ultralytics"

    if task == "segment":
        if tier < 3:
            return ("[vision/segment] Segmentation (SAM/DINO) requires T3 "
                    "(≥48 GB effective). Use task=detect on lower tiers.")
        try:
            from ultralytics import SAM  # type: ignore
            seg  = SAM("sam_b.pt")
            res  = seg(str(p))[0]
            return json.dumps({"model": "sam_b",
                               "n_masks": len(res.masks) if res.masks else 0})
        except ImportError:
            return "[vision/segment] Install: pip install ultralytics"

    return f"[vision] Unknown task '{task}'. Valid: classify | detect | ocr | segment"


# ══════════════════════════════════════════════════════════════════════════════
