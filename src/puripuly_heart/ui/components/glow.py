import flet as ft

from puripuly_heart.ui.theme import COLOR_PRIMARY


def create_glow_stack(content: ft.Control) -> ft.Stack:
    """
    Wraps the given content in a Stack with a 'Milky Glow' orb in the background.

    The glow is a transparent container with a large, soft BoxShadow positioned
    at the bottom-right corner. This creates a subtle, dispersed light effect
    that mimics optical blur (Gaussian Blur).

    Args:
        content: The foreground content control (e.g., Column, Row).

    Returns:
        ft.Stack: A Stack containing the glow layer and the content layer.
    """
    # Glow effect configuration
    # Opacity 0.05 (5%) + Blur 150 creates a very subtle, atmospheric depth.
    glow_orb = ft.Container(
        width=200,
        height=200,
        bgcolor=ft.Colors.TRANSPARENT,
        shadow=ft.BoxShadow(
            blur_radius=150,
            spread_radius=10,
            color=ft.Colors.with_opacity(0.05, COLOR_PRIMARY),
            offset=ft.Offset(0, 0),
        ),
        right=-50,
        bottom=-50,
    )

    return ft.Stack(
        controls=[
            glow_orb,
            ft.Container(
                content=content,
                expand=True,
                # Padding is handled by the parent container or inner content usually,
                # but to be safe and consistent with DisplayCard's previous logic,
                # we let the content handle its own internal padding if needed,
                # or we assume 'content' is already the main layout structure.
                # However, DisplayCard applied padding=32 to the container wrapping the stack currently?
                # No, DisplayCard had:
                # content=stack
                # stack = [glow, Container(content=main_content, padding=32)]
                # So we should probably allow passing padding or moving padding inside.
                # Let's keep it simple: This wrapper just puts the glow behind.
                # The 'content' passed in should be the layout that sits on top.
            ),
        ],
        expand=True,
    )
