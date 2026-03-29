use puripuly_heart_overlay::{
    CaptionBlock, CaptionChannel, CaptionLayoutPolicy, CaptionRenderer, OverlayPlacementPolicy,
};

fn test_block(text: &str) -> CaptionBlock {
    CaptionBlock::new("block-1", text)
}

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
fn renderer_layout_preserves_channel_metadata_for_visible_blocks() {
    let policy = CaptionLayoutPolicy::default();
    let result = policy.layout_blocks(
        vec![CaptionBlock::new("peer", "hello").with_channel(CaptionChannel::PeerChannel)],
        3840,
        1024,
    );

    assert_eq!(
        result.visible_blocks[0].channel,
        Some(CaptionChannel::PeerChannel)
    );
}

#[test]
fn renderer_default_caption_order_matches_self_and_peer_policy() {
    let policy = CaptionLayoutPolicy::default();
    assert_eq!(policy.compose_self_line("hello", "안녕"), "hello (안녕)");
    assert_eq!(policy.compose_peer_line("hello", "안녕"), "안녕 (hello)");
}

#[test]
fn renderer_channel_style_is_color_only_and_speaker_labels_are_hidden_by_default() {
    let policy = CaptionLayoutPolicy::default();
    assert!(policy.channel_uses_color_only());
    assert!(!policy.show_speaker_labels_by_default());
}

#[test]
fn openvr_overlay_policy_defaults_to_head_locked_mode() {
    let policy = OverlayPlacementPolicy::default();
    assert!(policy.is_head_locked());
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

#[test]
fn renderer_first_usable_frame_is_fully_transparent_before_real_caption_content() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let frame = renderer.render_empty_frame().unwrap();

    assert!(frame.is_fully_transparent());
}

#[cfg(windows)]
#[test]
fn renderer_returns_a_renderable_d3d11_texture_result() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let frame = renderer.render_blocks(vec![test_block("hello")]).unwrap();

    assert!(frame.texture_ptr().is_some());
    assert!(frame.d3d11_texture().is_some());
    assert_eq!(frame.width(), 3840);
    assert_eq!(frame.height(), 1024);
}

#[cfg(not(windows))]
#[test]
fn renderer_returns_a_renderable_texture_contract_off_windows() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let frame = renderer.render_blocks(vec![test_block("hello")]).unwrap();

    assert!(frame.texture_ptr().is_some());
    assert_eq!(frame.width(), 3840);
    assert_eq!(frame.height(), 1024);
}

#[cfg(not(windows))]
#[test]
fn renderer_runtime_backend_is_rejected_outside_windows() {
    let result = CaptionRenderer::new();
    assert!(result.is_err());
    let error = result.err().unwrap();

    assert!(
        error
            .to_string()
            .contains("Direct3D11 caption renderer is only available on Windows")
    );
}
