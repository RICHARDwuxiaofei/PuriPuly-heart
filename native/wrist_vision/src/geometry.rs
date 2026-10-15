pub type Pose = [[f32; 4]; 3];
pub const IDENTITY: Pose = [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 0.]];

pub fn transform(p: Pose, point: [f32; 3]) -> [f32; 3] {
    std::array::from_fn(|r| p[r][3] + (0..3).map(|c| p[r][c] * point[c]).sum::<f32>())
}

pub fn compose(a: Pose, b: Pose) -> Pose {
    std::array::from_fn(|r| {
        std::array::from_fn(|c| {
            (0..3).map(|k| a[r][k] * b[k][c]).sum::<f32>() + if c == 3 { a[r][3] } else { 0. }
        })
    })
}

pub fn inverse(a: Pose) -> Pose {
    std::array::from_fn(|r| {
        std::array::from_fn(|c| {
            if c < 3 {
                a[c][r]
            } else {
                -(0..3).map(|k| a[k][r] * a[k][3]).sum::<f32>()
            }
        })
    })
}

pub fn corners(pose: Pose, width: f32, height: f32) -> [[f32; 3]; 4] {
    [
        [-width / 2., height / 2., 0.],
        [width / 2., height / 2., 0.],
        [width / 2., -height / 2., 0.],
        [-width / 2., -height / 2., 0.],
    ]
    .map(|v| transform(pose, v))
}

pub fn project(
    world: [[f32; 3]; 4],
    eye: Pose,
    bounds: [f32; 4],
    size: [u32; 2],
) -> Result<[[f32; 2]; 4], &'static str> {
    let [l, r, t, b] = bounds;
    if !bounds.iter().all(|v| v.is_finite()) || l >= r || t >= b || size.contains(&0) {
        return Err("invalid_projection");
    }
    let mut quad = [[0.; 2]; 4];
    for (point, pixel) in world.into_iter().zip(&mut quad) {
        let p = transform(inverse(eye), point);
        if p.iter().any(|v| !v.is_finite()) || p[2] >= -0.01 {
            return Err("frame_behind_eye");
        }
        *pixel = [
            (p[0] / -p[2] - l) / (r - l) * size[0] as f32,
            (-p[1] / -p[2] - t) / (b - t) * size[1] as f32,
        ];
        if pixel[0] < 0.
            || pixel[1] < 0.
            || pixel[0] > (size[0] - 1) as f32
            || pixel[1] > (size[1] - 1) as f32
        {
            return Err("frame_outside_eye");
        }
    }
    let area = ((quad[1][0] - quad[0][0]) * (quad[3][1] - quad[0][1])
        - (quad[1][1] - quad[0][1]) * (quad[3][0] - quad[0][0]))
        .abs();
    if area < 64. {
        return Err("frame_too_small");
    }
    Ok(quad)
}

pub fn homography(q: [[f32; 2]; 4]) -> Result<[f64; 8], &'static str> {
    let mut a = [[0_f64; 9]; 8];
    for (i, ([x, y], [u, v])) in [[0., 0.], [1., 0.], [1., 1.], [0., 1.]]
        .into_iter()
        .zip(q)
        .enumerate()
    {
        let (u, v) = (u as f64, v as f64);
        a[2 * i] = [x, y, 1., 0., 0., 0., -u * x, -u * y, u];
        a[2 * i + 1] = [0., 0., 0., x, y, 1., -v * x, -v * y, v];
    }
    for col in 0..8 {
        let pivot = (col..8)
            .max_by(|i, j| a[*i][col].abs().total_cmp(&a[*j][col].abs()))
            .unwrap();
        a.swap(col, pivot);
        let scale = a[col][col];
        if !scale.is_finite() || scale.abs() < 1e-9 {
            return Err("degenerate_frame");
        }
        for k in col..9 {
            a[col][k] /= scale;
        }
        for row in 0..8 {
            if row != col {
                let f = a[row][col];
                for k in col..9 {
                    a[row][k] -= f * a[col][k];
                }
            }
        }
    }
    Ok(std::array::from_fn(|i| a[i][8]))
}

pub fn rectify(
    rgba: &[u8],
    size: [u32; 2],
    quad: [[f32; 2]; 4],
) -> Result<(Vec<u8>, [u32; 2]), &'static str> {
    if size.contains(&0)
        || size.iter().any(|n| *n > 8192)
        || rgba.len() != size[0] as usize * size[1] as usize * 4
    {
        return Err("invalid_capture_size");
    }
    let h = homography(quad)?;
    let distance =
        |a: [f32; 2], b: [f32; 2]| ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt();
    let w = distance(quad[0], quad[1]).max(distance(quad[3], quad[2]));
    let height = distance(quad[0], quad[3]).max(distance(quad[1], quad[2]));
    let scale = (1024. / w.max(height)).min(1.);
    let output = [
        (w * scale).round().max(2.) as u32,
        (height * scale).round().max(2.) as u32,
    ];
    if output.iter().any(|v| *v > 1024) {
        return Err("invalid_capture_size");
    }
    let mut rgb = Vec::with_capacity(output[0] as usize * output[1] as usize * 3);
    for y in 0..output[1] {
        for x in 0..output[0] {
            let (u, v) = (
                x as f64 / (output[0] - 1) as f64,
                y as f64 / (output[1] - 1) as f64,
            );
            let z = h[6] * u + h[7] * v + 1.;
            let (sx, sy) = (
                (h[0] * u + h[1] * v + h[2]) / z,
                (h[3] * u + h[4] * v + h[5]) / z,
            );
            if !sx.is_finite()
                || !sy.is_finite()
                || sx < -0.01
                || sy < -0.01
                || sx > (size[0] - 1) as f64 + 0.01
                || sy > (size[1] - 1) as f64 + 0.01
            {
                return Err("frame_outside_eye");
            }
            let (sx, sy) = (
                sx.clamp(0., (size[0] - 1) as f64),
                sy.clamp(0., (size[1] - 1) as f64),
            );
            let (ix, iy) = (sx.floor() as u32, sy.floor() as u32);
            let (fx, fy) = (sx - ix as f64, sy - iy as f64);
            for c in 0..3 {
                let sample = |xx: u32, yy: u32| rgba[((yy * size[0] + xx) * 4) as usize + c] as f64;
                let a = sample(ix, iy) * (1. - fx) + sample((ix + 1).min(size[0] - 1), iy) * fx;
                let b = sample(ix, (iy + 1).min(size[1] - 1)) * (1. - fx)
                    + sample((ix + 1).min(size[0] - 1), (iy + 1).min(size[1] - 1)) * fx;
                rgb.push((a * (1. - fy) + b * fy).round() as u8);
            }
        }
    }
    Ok((rgb, output))
}

pub fn stable(a: Pose, b: Pose) -> bool {
    a.iter()
        .flatten()
        .chain(b.iter().flatten())
        .all(|v| v.is_finite())
        && (0..3).map(|r| (a[r][3] - b[r][3]).powi(2)).sum::<f32>() < 0.0025
        && (0..3)
            .map(|r| (0..3).map(|c| (a[r][c] - b[r][c]).powi(2)).sum::<f32>())
            .sum::<f32>()
            < 0.006
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn projection_and_rectification() {
        let mut frame = IDENTITY;
        frame[2][3] = -1.;
        let q = project(
            corners(frame, 1., 1.),
            IDENTITY,
            [-1., 1., -1., 1.],
            [100, 100],
        )
        .unwrap();
        assert_eq!(q, [[25., 25.], [75., 25.], [75., 75.], [25., 75.]]);
        let rgba = vec![255; 100 * 100 * 4];
        let (rgb, size) = rectify(&rgba, [100, 100], q).unwrap();
        assert_eq!(size, [50, 50]);
        assert!(rgb.iter().all(|v| *v == 255));
    }
    #[test]
    fn invalid_geometry_and_motion() {
        assert!(homography([[0., 0.]; 4]).is_err());
        assert!(project([[0., 0., 1.]; 4], IDENTITY, [-1., 1., -1., 1.], [100, 100]).is_err());
        let mut moved = IDENTITY;
        moved[0][3] = 0.1;
        assert!(!stable(IDENTITY, moved));
        assert!(stable(IDENTITY, IDENTITY));
        moved[0][0] = f32::NAN;
        assert!(!stable(moved, moved));
    }
    #[test]
    fn pose_inverse_preserves_points() {
        let mut p = IDENTITY;
        p[0][3] = 3.;
        assert_eq!(
            transform(inverse(p), transform(p, [2., 1., -3.])),
            [2., 1., -3.]
        );
    }
}
