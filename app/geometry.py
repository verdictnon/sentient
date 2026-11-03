"""Image-plane geometry with explicit boundary and hysteresis rules."""

from dataclasses import dataclass
import math

from app.domain import check_point

Point = tuple[float, float]


def cross(a: Point, b: Point, p: Point) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def on_segment(a: Point, b: Point, p: Point) -> bool:
    return abs(cross(a, b, p)) <= 1e-9 and (
        min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9
        and min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9
    )


def intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    values = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    return (values[0] * values[1] < 0 and values[2] * values[3] < 0) or any((
        on_segment(a, b, c), on_segment(a, b, d),
        on_segment(c, d, a), on_segment(c, d, b),
    ))


@dataclass(frozen=True)
class Line:
    start: Point
    end: Point
    hysteresis_px: float = 5.0

    def __post_init__(self):
        check_point(self.start)
        check_point(self.end)
        if tuple(self.start) == tuple(self.end):
            raise ValueError("Entrance line must have nonzero length")
        if (isinstance(self.hysteresis_px, bool)
                or not math.isfinite(self.hysteresis_px) or self.hysteresis_px <= 0):
            raise ValueError("Hysteresis must be finite and positive")

    def side(self, point: Point) -> int:
        distance = cross(self.start, self.end, point) / math.dist(self.start, self.end)
        return 1 if distance >= self.hysteresis_px else (
            -1 if distance <= -self.hysteresis_px else 0
        )

    def crossed_segment(self, previous: Point, current: Point) -> bool:
        return intersects(previous, current, self.start, self.end)


@dataclass(frozen=True)
class Polygon:
    vertices: tuple[Point, ...]

    def __post_init__(self):
        if len(self.vertices) < 3:
            raise ValueError("A polygon needs at least three vertices")
        for point in self.vertices:
            check_point(point)
        if len(set(map(tuple, self.vertices))) != len(self.vertices):
            raise ValueError("Polygon vertices must be distinct; do not repeat the first")
        edges = list(zip(self.vertices, self.vertices[1:] + self.vertices[:1]))
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
        if abs(area) <= 1e-9:
            raise ValueError("Polygon must have nonzero area")
        for i, (a, b) in enumerate(edges):
            for j in range(i + 1, len(edges)):
                if j == i + 1 or (i == 0 and j == len(edges) - 1):
                    continue
                if intersects(a, b, *edges[j]):
                    raise ValueError("Polygon must not self-intersect")
        xs = [p[0] for p in self.vertices]
        ys = [p[1] for p in self.vertices]
        # Saved aside because analytics tests every footpoint against every area.
        object.__setattr__(self, "bounds", (min(xs), min(ys), max(xs), max(ys)))

    def contains(self, point: Point) -> bool:
        left, top, right, bottom = self.bounds
        if not (left - 1e-9 <= point[0] <= right + 1e-9 and top - 1e-9 <= point[1] <= bottom + 1e-9):
            return False
        inside = False
        for a, b in zip(self.vertices, self.vertices[1:] + self.vertices[:1]):
            if on_segment(a, b, point):
                return True
            if (a[1] > point[1]) != (b[1] > point[1]):
                x = a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if point[0] < x:
                    inside = not inside
        return inside
