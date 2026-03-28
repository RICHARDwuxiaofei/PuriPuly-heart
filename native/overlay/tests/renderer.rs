use puripuly_heart_overlay::{CaptionBlock, CaptionLayoutPolicy};

fn long_block(id: &str) -> CaptionBlock {
    let text = "streaming translation captions should keep the newest utterance readable while \
                older blocks are dropped when the fixed overlay surface overflows "
        .repeat(24);
    CaptionBlock::new(id, text)
}

#[test]
fn renderer_default_caption_weight_order_excludes_bold() {
    let policy = CaptionLayoutPolicy::default();
    assert_eq!(
        policy.preferred_weights(),
        vec!["Semibold", "Medium", "Regular"]
    );
}

#[test]
fn renderer_preferred_face_resolution_uses_latin_and_cjk_order_before_system_fallback() {
    let policy = CaptionLayoutPolicy::default();

    assert_eq!(policy.latin_face_chain()[0], "Noto Sans");
    assert_eq!(
        policy.latin_face_chain().last(),
        Some(&"DirectWrite system fallback")
    );
    assert!(policy.cjk_face_chain().contains(&"Segoe UI"));
    assert_eq!(policy.cjk_face_chain()[0], "Noto Sans CJK KR");
}

#[test]
fn renderer_uses_fixed_surface_defaults_for_mvp_caption_layout() {
    let policy = CaptionLayoutPolicy::default();
    assert_eq!(policy.default_surface_size(), (3840, 1024));
    assert_eq!(policy.visible_window_target_blocks(), 2);
}

#[test]
fn renderer_limits_default_visible_window_to_the_two_newest_blocks() {
    let policy = CaptionLayoutPolicy::default();
    let result = policy.layout_blocks(
        vec![
            CaptionBlock::new("old", "short one"),
            CaptionBlock::new("mid", "short two"),
            CaptionBlock::new("new", "short three"),
        ],
        3840,
        4096,
    );

    assert_eq!(
        result
            .visible_blocks
            .iter()
            .map(|block| block.id.as_str())
            .collect::<Vec<_>>(),
        vec!["mid", "new"]
    );
    assert!(result.dropped_block_ids.contains(&"old".to_string()));
}

#[test]
fn renderer_overflow_drops_oldest_block_before_truncating_newest_content() {
    let policy = CaptionLayoutPolicy::default();
    let result = policy.layout_blocks(vec![long_block("old"), long_block("new")], 3840, 1024);

    assert!(result.visible_blocks.iter().any(|block| block.id == "new"));
    assert!(result.dropped_block_ids.contains(&"old".into()));
    assert!(
        result
            .visible_blocks
            .iter()
            .find(|block| block.id == "new")
            .is_some_and(|block| !block.lines.is_empty())
    );
}
