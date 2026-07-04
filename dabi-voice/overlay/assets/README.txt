Drop Dabi's mouth-flap art here:

  dabi_closed.png   — mouth closed (also used while idle)
  dabi_open.png     — mouth open

Transparent PNGs, same dimensions, same registration (the images are
stacked exactly on top of each other and toggled). Until both files
exist the overlay shows a placeholder unicorn emoji so the pipeline can
be tested without art.

When the commissioned Live2D model arrives, its runtime files
(.moc3 / .model3.json / textures / physics) can live in here too —
the renderer swap happens in overlay.js (see makePngRenderer).
