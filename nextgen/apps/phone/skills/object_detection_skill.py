"""目标检测技能实现。"""

from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from typing import List, Optional, Sequence, Tuple

from nextgen.shared.models.detection import (
    BoundingBox,
    DetectionResult,
    FindObjectFrameAnalysis,
    HandObservation,
    ObjectObservation,
)

Point = Tuple[float, float]
Polygon = Sequence[Point]
HandBox = Tuple[float, float, float, float]
LandmarkPoint = Tuple[float, float]


@dataclass
class ObjectDetectionSkill:
    """目标检测技能。

    主要功能：
    - 为寻找物体任务提供统一的检测结果输出
    - 承接从旧 `yolomedia.py` 迁移出的核心引导判断逻辑

    当前阶段：
    - 不接真实模型推理
    - 已支持根据外部分析结果生成稳定的检测结果与引导方向
    """

    def detect(self, session_id: str, target_name: str) -> DetectionResult:
        """执行目标检测。

        参数：
        - session_id：任务实例标识
        - target_name：目标名称

        返回值：
        - 一个占位检测结果。
        """

        return DetectionResult(
            session_id=session_id,
            result_type="object_detection",
            timestamp=datetime.now().astimezone().isoformat(),
            target_name=target_name,
            found=False,
            position="unknown",
            score=0.0,
        )

    def build_object_observation(
        self,
        polygon: Polygon,
        score: float = 0.0,
    ) -> Optional[ObjectObservation]:
        """根据目标多边形构造统一观测对象。"""

        center, area = self.calculate_polygon_center_and_area(polygon)
        if center is None:
            return None

        return ObjectObservation(
            center_x=center[0],
            center_y=center[1],
            area=area,
            polygon=[[float(x), float(y)] for x, y in polygon],
            score=score,
        )

    def build_hand_observation(
        self,
        landmarks: Sequence[LandmarkPoint],
        frame_width: int,
        frame_height: int,
    ) -> Optional[HandObservation]:
        """根据手部关键点构造统一观测对象。"""

        bbox, area = self.calculate_hand_bbox_and_area(landmarks, frame_width, frame_height)
        if bbox is None:
            return None

        center_x = bbox.x1 + ((bbox.x2 - bbox.x1) / 2.0)
        center_y = bbox.y1 + ((bbox.y2 - bbox.y1) / 2.0)
        grasp_detected, grasp_score = self.detect_grasp_from_landmarks(
            landmarks=landmarks,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        return HandObservation(
            center_x=center_x,
            center_y=center_y,
            area=area,
            bbox=bbox,
            grasp_detected=grasp_detected,
            grasp_score=grasp_score,
        )

    def select_primary_object_observation(
        self,
        candidates: Sequence[ObjectObservation],
    ) -> Optional[ObjectObservation]:
        """从候选目标中选择当前主目标。

        当前阶段策略：
        - 与旧 `yolomedia.py` 一致，优先选择面积最大的候选目标
        - 若面积相同，则优先选择分数更高者
        """

        valid_candidates = [candidate for candidate in candidates if candidate is not None]
        if not valid_candidates:
            return None

        return max(
            valid_candidates,
            key=lambda candidate: (candidate.area, candidate.score),
        )

    def build_frame_analysis(
        self,
        frame_width: int,
        frame_height: int,
        target_name: str,
        candidates: Sequence[ObjectObservation],
        hand_observation: Optional[HandObservation] = None,
        source: str = "legacy_yolomedia",
    ) -> FindObjectFrameAnalysis:
        """根据候选目标和手部观测构造单帧分析输入。

        说明：
        - 这一层对应旧 `yolomedia.py` 中 `candidate_masks` 的归一化处理
        - 主循环不再需要自己决定“选哪个目标”，统一交给技能层
        """

        primary_object = self.select_primary_object_observation(candidates)
        return FindObjectFrameAnalysis(
            frame_width=frame_width,
            frame_height=frame_height,
            target_name=target_name,
            found=primary_object is not None,
            object_observation=primary_object,
            hand_observation=hand_observation,
            candidate_count=len([candidate for candidate in candidates if candidate is not None]),
            source=source,
        )

    def build_frame_analysis_from_image(
        self,
        frame,
        target_name: str,
        source: str = "peer_stream_raw_frame",
    ) -> FindObjectFrameAnalysis:
        """从原始图像帧构造单帧分析输入。

        说明：
        - 该入口用于把测试支持服务上传的图片/视频帧真正接到找物检测链路
        - 当前阶段采用轻量 OpenCV 启发式检测，而不是直接依赖重模型推理
        """

        frame_height, frame_width = frame.shape[:2]
        candidates = self.extract_object_candidates_from_image(frame=frame, target_name=target_name)
        return self.build_frame_analysis(
            frame_width=frame_width,
            frame_height=frame_height,
            target_name=target_name,
            candidates=candidates,
            hand_observation=None,
            source=source,
        )

    def extract_object_candidates_from_image(
        self,
        frame,
        target_name: str,
    ) -> List[ObjectObservation]:
        """从原始图像帧中提取候选目标。

        当前阶段策略：
        - 使用边缘 + 形态学闭运算提取显著区域
        - 对矩形度和长宽比做简单打分
        - 若目标名称看起来像手机，则对“手机形状”的候选做额外加权
        """

        import cv2
        import numpy as np

        frame_height, frame_width = frame.shape[:2]
        frame_area = float(frame_width * frame_height)
        if frame_area <= 0:
            return []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 60, 160)
        kernel = np.ones((5, 5), dtype=np.uint8)
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates: List[ObjectObservation] = []
        for contour in contours:
            contour_area = float(cv2.contourArea(contour))
            if contour_area < frame_area * 0.015:
                continue

            x, y, width, height = cv2.boundingRect(contour)
            if width <= 0 or height <= 0:
                continue

            rect_area = float(width * height)
            fill_ratio = contour_area / rect_area if rect_area > 0 else 0.0
            aspect_ratio = max(width, height) / max(1.0, min(width, height))
            phone_shape_bonus = self._score_phone_shape(target_name=target_name, aspect_ratio=aspect_ratio)
            score = min(
                0.99,
                max(
                    0.1,
                    0.35
                    + min(0.35, contour_area / max(frame_area * 0.25, 1.0))
                    + min(0.2, fill_ratio * 0.2)
                    + phone_shape_bonus,
                ),
            )

            polygon = cv2.approxPolyDP(contour, epsilon=0.02 * cv2.arcLength(contour, True), closed=True)
            points = polygon.reshape(-1, 2).tolist() if polygon is not None and len(polygon) >= 3 else [
                [x, y],
                [x + width, y],
                [x + width, y + height],
                [x, y + height],
            ]
            observation = self.build_object_observation(points, score=score)
            if observation is None:
                continue
            position, _ = self.get_center_guidance(
                object_center=(observation.center_x, observation.center_y),
                frame_center=(frame_width / 2.0, frame_height / 2.0),
                threshold=max(20, int(min(frame_width, frame_height) * 0.08)),
            )
            observation.position = position
            candidates.append(observation)

        return sorted(candidates, key=lambda item: (item.area, item.score), reverse=True)

    def _score_phone_shape(self, target_name: str, aspect_ratio: float) -> float:
        """根据目标名称和长宽比为手机形状做简单加权。"""

        normalized_target = "".join(str(target_name).lower().split())
        if not any(keyword in normalized_target for keyword in ("手机", "phone", "iphone", "android")):
            return 0.0
        if 1.4 <= aspect_ratio <= 2.6:
            return 0.18
        if 1.2 <= aspect_ratio <= 3.0:
            return 0.08
        return 0.0

    def detect_from_analysis(
        self,
        session_id: str,
        target_name: str,
        found: bool,
        score: float = 0.0,
        object_center: Optional[Point] = None,
        frame_size: Optional[Tuple[int, int]] = None,
        hand_center: Optional[Point] = None,
        hand_area: float = 0.0,
        object_area: float = 0.0,
        hand_box: Optional[HandBox] = None,
        polygon: Optional[Polygon] = None,
    ) -> DetectionResult:
        """根据外部分析结果构造检测结果。

        说明：
        - 该方法用于第二阶段迁移 `yolomedia.py` 中的核心判断逻辑
        - 真实模型推理结果可在后续阶段接到这里
        """

        position = "unknown"
        guidance_direction = None
        secondary_direction = None
        contact_ratio = 0.0

        if found and object_center is not None and frame_size is not None:
            frame_center = (frame_size[0] / 2.0, frame_size[1] / 2.0)
            position, _ = self.get_center_guidance(object_center, frame_center)

        if found:
            guidance_direction, secondary_direction, contact_ratio = self.get_guidance_direction(
                hand_center=hand_center,
                object_center=object_center,
                hand_area=hand_area,
                object_area=object_area,
                hand_box=hand_box,
                polygon=polygon,
            )

        return DetectionResult(
            session_id=session_id,
            result_type="object_detection",
            timestamp=datetime.now().astimezone().isoformat(),
            target_name=target_name,
            found=found,
            position=position,
            score=score,
            extra={
                "object_center": list(object_center) if object_center else None,
                "frame_size": list(frame_size) if frame_size else None,
                "hand_center": list(hand_center) if hand_center else None,
                "guidance_direction": guidance_direction,
                "secondary_direction": secondary_direction,
                "contact_ratio": round(contact_ratio, 4),
            },
        )

    def detect_from_frame_analysis(
        self,
        session_id: str,
        analysis: FindObjectFrameAnalysis,
    ) -> DetectionResult:
        """根据单帧分析输入构造检测结果。

        说明：
        - 这是旧 `yolomedia.py` 主循环接入新技能的主要适配入口
        - 主循环只需要先把零散变量整理成 `FindObjectFrameAnalysis`
        """

        object_center = None
        hand_center = None
        hand_box = None
        object_area = 0.0
        hand_area = 0.0
        score = 0.0
        polygon = None

        if analysis.object_observation is not None:
            object_center = (
                analysis.object_observation.center_x,
                analysis.object_observation.center_y,
            )
            object_area = analysis.object_observation.area
            score = analysis.object_observation.score
            if analysis.object_observation.polygon:
                polygon = [tuple(point) for point in analysis.object_observation.polygon]

        if analysis.hand_observation is not None:
            hand_center = (
                analysis.hand_observation.center_x,
                analysis.hand_observation.center_y,
            )
            hand_area = analysis.hand_observation.area
            hand_box = (
                analysis.hand_observation.bbox.x1,
                analysis.hand_observation.bbox.y1,
                analysis.hand_observation.bbox.x2 - analysis.hand_observation.bbox.x1,
                analysis.hand_observation.bbox.y2 - analysis.hand_observation.bbox.y1,
            )

        result = self.detect_from_analysis(
            session_id=session_id,
            target_name=analysis.target_name,
            found=analysis.found,
            score=score,
            object_center=object_center,
            frame_size=(analysis.frame_width, analysis.frame_height),
            hand_center=hand_center,
            hand_area=hand_area,
            object_area=object_area,
            hand_box=hand_box,
            polygon=polygon,
        )
        result.extra["candidate_count"] = analysis.candidate_count
        result.extra["source"] = analysis.source
        if analysis.hand_observation is not None:
            result.extra["grasp_detected"] = analysis.hand_observation.grasp_detected
            result.extra["grasp_score"] = round(analysis.hand_observation.grasp_score, 4)
        return result

    def get_center_guidance(
        self,
        object_center: Optional[Point],
        frame_center: Optional[Point],
        threshold: int = 30,
    ) -> Tuple[str, bool]:
        """判断目标相对画面中心的位置。

        返回值：
        - 第一个值为位置标签
        - 第二个值表示是否已基本居中
        """

        if object_center is None or frame_center is None:
            return "unknown", False

        ox, oy = object_center
        fx, fy = frame_center
        dx = ox - fx
        dy = oy - fy

        horizontal = None
        vertical = None
        if abs(dx) > threshold:
            horizontal = "right" if dx > 0 else "left"
        if abs(dy) > threshold:
            vertical = "down" if dy > 0 else "up"

        if horizontal and vertical:
            return f"{vertical}_{horizontal}", False
        if horizontal:
            return horizontal, False
        if vertical:
            return vertical, False
        return "center", True

    def calculate_polygon_center_and_area(
        self,
        polygon: Optional[Polygon],
    ) -> Tuple[Optional[Point], float]:
        """计算多边形中心点和面积。"""

        if polygon is None or len(polygon) < 3:
            return None, 0.0

        points = [(float(x), float(y)) for x, y in polygon]
        area_twice = 0.0
        center_x = 0.0
        center_y = 0.0

        for index in range(len(points)):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % len(points)]
            cross = (x1 * y2) - (x2 * y1)
            area_twice += cross
            center_x += (x1 + x2) * cross
            center_y += (y1 + y2) * cross

        if abs(area_twice) < 1e-6:
            avg_x = sum(point[0] for point in points) / len(points)
            avg_y = sum(point[1] for point in points) / len(points)
            return (avg_x, avg_y), 0.0

        area = abs(area_twice) / 2.0
        centroid_x = center_x / (3.0 * area_twice)
        centroid_y = center_y / (3.0 * area_twice)
        return (centroid_x, centroid_y), area

    def calculate_hand_bbox_and_area(
        self,
        landmarks: Sequence[LandmarkPoint],
        frame_width: int,
        frame_height: int,
    ) -> Tuple[Optional[BoundingBox], float]:
        """根据手部关键点计算手框和面积。"""

        xs = [int(point[0] * frame_width) for point in landmarks]
        ys = [int(point[1] * frame_height) for point in landmarks]
        if not xs or not ys:
            return None, 0.0

        x0 = min(xs)
        y0 = min(ys)
        x1 = max(xs)
        y1 = max(ys)
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        bbox = BoundingBox(x1=x0, y1=y0, x2=x0 + width, y2=y0 + height)
        return bbox, float(width * height)

    def detect_grasp_from_landmarks(
        self,
        landmarks: Sequence[LandmarkPoint],
        frame_width: int,
        frame_height: int,
    ) -> Tuple[bool, float]:
        """根据手部关键点做轻量握持判断。"""

        bbox, _ = self.calculate_hand_bbox_and_area(landmarks, frame_width, frame_height)
        if bbox is None:
            return False, 0.0

        hand_diag = sqrt(((bbox.x2 - bbox.x1) ** 2) + ((bbox.y2 - bbox.y1) ** 2)) + 1e-6
        palm_indices = [0, 5, 9, 13, 17]
        palm_x = sum(landmarks[index][0] * frame_width for index in palm_indices) / len(palm_indices)
        palm_y = sum(landmarks[index][1] * frame_height for index in palm_indices) / len(palm_indices)
        thumb_tip = (landmarks[4][0] * frame_width, landmarks[4][1] * frame_height)
        index_tip = (landmarks[8][0] * frame_width, landmarks[8][1] * frame_height)

        thumb_index_dist = self._distance(thumb_tip, index_tip) / hand_diag
        curled_distances = []
        for index in (12, 16, 20):
            fingertip = (landmarks[index][0] * frame_width, landmarks[index][1] * frame_height)
            curled_distances.append(
                self._distance(fingertip, (palm_x, palm_y)) / hand_diag
            )

        thumb_index_close = 0.34
        fingertip_near = 0.44
        min_curled_count = 1
        curled_count = sum(1 for distance in curled_distances if distance < fingertip_near)
        cond1 = thumb_index_dist < thumb_index_close
        cond2 = curled_count >= min_curled_count
        score = (
            0.5 * (1.0 - min(thumb_index_dist / thumb_index_close, 1.0))
            + 0.5 * min(curled_count / 3.0, 1.0)
        )
        return cond1 and cond2, score

    def get_guidance_direction(
        self,
        hand_center: Optional[Point],
        object_center: Optional[Point],
        hand_area: float,
        object_area: float,
        hand_box: Optional[HandBox] = None,
        polygon: Optional[Polygon] = None,
    ) -> Tuple[Optional[str], Optional[str], float]:
        """根据手心和目标位置生成引导方向。

        返回值：
        - 主引导方向
        - 次级补充方向
        - 接触比例
        """

        if hand_center is None or object_center is None:
            return None, None, 0.0

        is_touching = False
        overlap_ratio = 0.0
        if hand_box is not None and polygon is not None:
            is_touching, overlap_ratio = self.check_hand_object_contact(
                hand_box=hand_box,
                polygon=polygon,
                overlap_threshold=0.1,
            )

        hx, hy = hand_center
        ox, oy = object_center
        dx = ox - hx
        dy = oy - hy

        if is_touching:
            return "向前", f"接触度: {overlap_ratio:.1%}", overlap_ratio

        horizontal_threshold = 30
        vertical_threshold = 30
        horizontal_direction = None
        vertical_direction = None

        if abs(dx) > horizontal_threshold:
            horizontal_direction = "向右" if dx > 0 else "向左"

        if abs(dy) > vertical_threshold:
            vertical_direction = "向下" if dy > 0 else "向上"

        if abs(dx) > abs(dy) and horizontal_direction:
            return horizontal_direction, vertical_direction, overlap_ratio
        if vertical_direction:
            return vertical_direction, horizontal_direction, overlap_ratio

        distance = sqrt(dx ** 2 + dy ** 2)
        if distance < 50:
            return "向前", "请缓慢靠近", overlap_ratio
        return "保持", None, overlap_ratio

    def check_hand_object_contact(
        self,
        hand_box: HandBox,
        polygon: Polygon,
        overlap_threshold: float = 0.15,
    ) -> Tuple[bool, float]:
        """检测手框与目标区域是否发生接触。

        当前阶段采用轻量近似算法：
        - 先计算目标多边形的包围框
        - 再计算与手框的交集面积
        - 用交集面积占手框面积的比例作为接触比例
        """

        if hand_box is None or polygon is None or len(polygon) < 3:
            return False, 0.0

        hand_x, hand_y, hand_w, hand_h = hand_box
        hand_area = max(1.0, hand_w * hand_h)
        poly_box = self._polygon_bbox(polygon)
        if poly_box is None:
            return False, 0.0

        overlap_area = self._rect_intersection_area(
            (hand_x, hand_y, hand_w, hand_h),
            poly_box,
        )
        overlap_ratio = overlap_area / hand_area
        return overlap_ratio > overlap_threshold, overlap_ratio

    def _polygon_bbox(self, polygon: Polygon) -> Optional[HandBox]:
        """计算目标多边形包围框。"""

        if polygon is None or len(polygon) < 3:
            return None
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        min_x = min(xs)
        max_x = max(xs)
        min_y = min(ys)
        max_y = max(ys)
        return (min_x, min_y, max_x - min_x, max_y - min_y)

    def _rect_intersection_area(self, rect_a: HandBox, rect_b: HandBox) -> float:
        """计算两个矩形的交集面积。"""

        ax, ay, aw, ah = rect_a
        bx, by, bw, bh = rect_b

        left = max(ax, bx)
        top = max(ay, by)
        right = min(ax + aw, bx + bw)
        bottom = min(ay + ah, by + bh)

        if right <= left or bottom <= top:
            return 0.0
        return float((right - left) * (bottom - top))

    def _distance(self, point_a: Point, point_b: Point) -> float:
        """计算两点距离。"""

        return sqrt(((point_a[0] - point_b[0]) ** 2) + ((point_a[1] - point_b[1]) ** 2))
