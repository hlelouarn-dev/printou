{
  "name": "Avec logo vedette Harcour",
  "orientation": "paysage",
  "margins": {
    "top": 4.0,
    "bottom": 8.0,
    "left": 4.0,
    "right": 4.0
  },
  "background_color": "#000000",
  "max_stretch_pct": 3.0,
  "layers": [
    {
      "kind": "logo",
      "name": "logo_vedette",
      "asset_name": "logo_harcour.png",
      "reference": "paper",
      "pos_x_pct": 96.0,
      "pos_y_pct": 1.5,
      "anchor": "rt",
      "width_pct": 9.0,
      "height_pct": 0.0,
      "opacity": 1.0,
      "z_order": 10
    },
    {
      "kind": "text",
      "name": "info_event",
      "content": "{event_name} - {event_location} - {event_date}",
      "reference": "image",
      "pos_x_pct": 100.0,
      "pos_y_pct": 100.0,
      "anchor": "rt",
      "font_family": "Segoe UI",
      "font_size_pct": 1.8,
      "color": "#FFFFFF",
      "bold": true,
      "italic": false,
      "z_order": 10
    }
  ]
}