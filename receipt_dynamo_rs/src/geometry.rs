//! Geometry value types and transforms used by OCR token entities.

use std::f64::consts::PI;

use crate::error::{Error, Result};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BoundingBox {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl BoundingBox {
    pub fn new(x: f64, y: f64, width: f64, height: f64) -> Result<Self> {
        if ![x, y, width, height].iter().all(|v| v.is_finite()) {
            return Err(Error::validation("bounding_box values must be finite"));
        }
        Ok(Self {
            x,
            y,
            width,
            height,
        })
    }

    pub fn contains(self, x: f64, y: f64) -> bool {
        self.x <= x && x <= self.x + self.width && self.y <= y && y <= self.y + self.height
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Corners {
    pub top_left: crate::entities::Point,
    pub top_right: crate::entities::Point,
    pub bottom_left: crate::entities::Point,
    pub bottom_right: crate::entities::Point,
}

impl Corners {
    pub fn centroid(self) -> crate::entities::Point {
        crate::entities::Point {
            x: (self.top_left.x + self.top_right.x + self.bottom_left.x + self.bottom_right.x)
                / 4.0,
            y: (self.top_left.y + self.top_right.y + self.bottom_left.y + self.bottom_right.y)
                / 4.0,
        }
    }

    pub fn bounding_box(self) -> BoundingBox {
        let xs = [
            self.top_left.x,
            self.top_right.x,
            self.bottom_left.x,
            self.bottom_right.x,
        ];
        let ys = [
            self.top_left.y,
            self.top_right.y,
            self.bottom_left.y,
            self.bottom_right.y,
        ];
        let min_x = xs.iter().copied().fold(f64::INFINITY, f64::min);
        let max_x = xs.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let min_y = ys.iter().copied().fold(f64::INFINITY, f64::min);
        let max_y = ys.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        BoundingBox {
            x: min_x,
            y: min_y,
            width: max_x - min_x,
            height: max_y - min_y,
        }
    }

    fn corners_mut(&mut self) -> [&mut crate::entities::Point; 4] {
        [
            &mut self.top_left,
            &mut self.top_right,
            &mut self.bottom_left,
            &mut self.bottom_right,
        ]
    }
}

/// Mutable geometry shared by line/word/letter entities.
#[derive(Clone, Debug, PartialEq)]
pub struct Geometry {
    pub bounding_box: BoundingBox,
    pub corners: Corners,
    pub angle_degrees: f64,
    pub angle_radians: f64,
}

impl Geometry {
    pub fn validate(&self) -> Result<()> {
        if !self.angle_degrees.is_finite() {
            return Err(Error::validation("angle_degrees must be float or int"));
        }
        if !self.angle_radians.is_finite() {
            return Err(Error::validation("angle_radians must be float or int"));
        }
        Ok(())
    }

    pub fn translate(&mut self, x: f64, y: f64) {
        for corner in self.corners.corners_mut() {
            corner.x += x;
            corner.y += y;
        }
        self.bounding_box.x += x;
        self.bounding_box.y += y;
    }

    pub fn scale(&mut self, sx: f64, sy: f64) {
        for corner in self.corners.corners_mut() {
            corner.x *= sx;
            corner.y *= sy;
        }
        self.bounding_box.x *= sx;
        self.bounding_box.y *= sy;
        self.bounding_box.width *= sx;
        self.bounding_box.height *= sy;
    }

    pub fn rotate(
        &mut self,
        angle: f64,
        origin_x: f64,
        origin_y: f64,
        use_radians: bool,
    ) -> Result<()> {
        let theta = if use_radians {
            if !(-PI / 2.0..=PI / 2.0).contains(&angle) {
                return Err(Error::validation(format!(
                    "Angle {angle} radians is outside [-pi/2, pi/2]"
                )));
            }
            angle
        } else {
            if !(-90.0..=90.0).contains(&angle) {
                return Err(Error::validation(format!(
                    "Angle {angle} degrees is outside [-90, 90]"
                )));
            }
            angle.to_radians()
        };
        let (sin_t, cos_t) = theta.sin_cos();
        for corner in self.corners.corners_mut() {
            let tx = corner.x - origin_x;
            let ty = corner.y - origin_y;
            corner.x = tx * cos_t - ty * sin_t + origin_x;
            corner.y = tx * sin_t + ty * cos_t + origin_y;
        }
        self.update_bounding_box_from_corners();
        self.angle_degrees += if use_radians {
            theta.to_degrees()
        } else {
            angle
        };
        self.angle_radians += theta;
        self.normalize_angles();
        Ok(())
    }

    pub fn shear(&mut self, shx: f64, shy: f64, pivot_x: f64, pivot_y: f64) {
        for corner in self.corners.corners_mut() {
            let (x, y) = shear_point(corner.x, corner.y, pivot_x, pivot_y, shx, shy);
            corner.x = x;
            corner.y = y;
        }
        self.update_bounding_box_from_corners();
    }

    pub fn rotate_90_ccw_in_place(&mut self) {
        for corner in self.corners.corners_mut() {
            let (x, y) = (corner.x, corner.y);
            corner.x = y;
            corner.y = -(x - 1.0);
        }
        self.update_bounding_box_from_corners();
        self.angle_degrees += 90.0;
        self.angle_radians += PI / 2.0;
    }

    pub fn warp_affine(&mut self, a: f64, b: f64, c: f64, d: f64, e: f64, f: f64) {
        for corner in self.corners.corners_mut() {
            let nx = a * corner.x + b * corner.y + c;
            let ny = d * corner.x + e * corner.y + f;
            corner.x = nx;
            corner.y = ny;
        }
        self.update_bounding_box_from_corners();
        let (dx, dy) = {
            let nx = a * 1.0 + b * 0.0 + c;
            let ny = d * 1.0 + e * 0.0 + f;
            let origin_x = a * 0.0 + b * 0.0 + c;
            let origin_y = d * 0.0 + e * 0.0 + f;
            (nx - origin_x, ny - origin_y)
        };
        self.angle_radians = dy.atan2(dx);
        self.angle_degrees = self.angle_radians.to_degrees();
    }

    pub fn inverse_perspective_transform(
        &mut self,
        a: f64,
        b: f64,
        c: f64,
        d: f64,
        e: f64,
        f: f64,
        g: f64,
        h: f64,
        src_width: u32,
        src_height: u32,
        dst_width: u32,
        dst_height: u32,
        flip_y: bool,
    ) -> Result<()> {
        if src_width == 0 || src_height == 0 || dst_width == 0 || dst_height == 0 {
            return Err(Error::validation(
                "perspective dimensions must be positive integers",
            ));
        }
        for corner in self.corners.corners_mut() {
            let x_new_px = corner.x * f64::from(dst_width);
            let mut y_new_px = corner.y * f64::from(dst_height);
            if flip_y {
                y_new_px = f64::from(dst_height) - y_new_px;
            }
            let a11 = g * x_new_px - a;
            let a12 = h * x_new_px - b;
            let b1 = c - x_new_px;
            let a21 = g * y_new_px - d;
            let a22 = h * y_new_px - e;
            let b2 = f - y_new_px;
            let det = a11 * a22 - a12 * a21;
            if det.abs() < 1e-12 {
                return Err(Error::validation(
                    "Inverse perspective transform is singular for this corner",
                ));
            }
            let x_old_px = (b1 * a22 - b2 * a12) / det;
            let y_old_px = (a11 * b2 - a21 * b1) / det;
            corner.x = x_old_px / f64::from(src_width);
            corner.y = y_old_px / f64::from(src_height);
            if flip_y {
                corner.y = 1.0 - corner.y;
            }
        }
        self.update_bounding_box_from_corners();
        let dx = self.corners.top_right.x - self.corners.top_left.x;
        let dy = self.corners.top_right.y - self.corners.top_left.y;
        self.angle_radians = dy.atan2(dx);
        self.angle_degrees = self.angle_radians * 180.0 / PI;
        Ok(())
    }

    pub fn update_bounding_box_from_corners(&mut self) {
        self.bounding_box = self.corners.bounding_box();
    }

    pub fn normalize_angles(&mut self) {
        while self.angle_degrees > 180.0 {
            self.angle_degrees -= 360.0;
        }
        while self.angle_degrees < -180.0 {
            self.angle_degrees += 360.0;
        }
        while self.angle_radians > PI {
            self.angle_radians -= 2.0 * PI;
        }
        while self.angle_radians < -PI {
            self.angle_radians += 2.0 * PI;
        }
    }

    pub fn diagonal_length(&self) -> f64 {
        let dx = self.corners.top_right.x - self.corners.bottom_left.x;
        let dy = self.corners.top_right.y - self.corners.bottom_left.y;
        dx.hypot(dy)
    }
}

pub fn shear_point(px: f64, py: f64, pivot_x: f64, pivot_y: f64, shx: f64, shy: f64) -> (f64, f64) {
    let translated_x = px - pivot_x;
    let translated_y = py - pivot_y;
    (
        translated_x + shx * translated_y + pivot_x,
        shy * translated_x + translated_y + pivot_y,
    )
}

/// Normalize an address for consistent places-cache keys.
pub fn normalize_address(addr: &str) -> String {
    let mut addr = addr.to_lowercase();
    static REPLACEMENTS: &[(&str, &str)] = &[
        ("blvd.", "boulevard"),
        ("blvd", "boulevard"),
        ("st.", "street"),
        ("st", "street"),
        ("ave.", "avenue"),
        ("ave", "avenue"),
        ("rd.", "road"),
        ("rd", "road"),
    ];
    // Word-boundary replacements matching Python \b...\b
    for (from, to) in REPLACEMENTS {
        addr = replace_word(&addr, from, to);
    }
    let mut cleaned = String::with_capacity(addr.len());
    for ch in addr.chars() {
        if ch.is_ascii_alphanumeric() || ch.is_whitespace() || ch == '_' {
            cleaned.push(ch);
        }
    }
    cleaned.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn replace_word(haystack: &str, word: &str, replacement: &str) -> String {
    let mut out = String::with_capacity(haystack.len());
    let lower = haystack.as_bytes();
    let needle = word.as_bytes();
    let mut i = 0;
    while i < lower.len() {
        if let Some(rest) = haystack.get(i..) {
            let rest_l = rest.to_ascii_lowercase();
            if rest_l.starts_with(word) {
                let before_ok = i == 0 || !is_word_char(lower[i - 1]);
                let after_idx = i + needle.len();
                let after_ok = after_idx >= lower.len() || !is_word_char(lower[after_idx]);
                if before_ok && after_ok {
                    out.push_str(replacement);
                    i = after_idx;
                    continue;
                }
            }
        }
        out.push(haystack[i..].chars().next().unwrap());
        i += haystack[i..].chars().next().unwrap().len_utf8();
    }
    out
}

fn is_word_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}
