<?php
$lagano_path = trim((string) wp_parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH), '/');
$lagano_view = $lagano_path === 'app' ? 'app' : 'landing';
if ($lagano_view === 'app') status_header(200);
?>
<!doctype html><html lang="hr"><head><meta charset="<?php bloginfo('charset'); ?>"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Pronađi svoju sljedeću šetnju središtem Zagreba. Stvarni pješački putovi, javni podaci i jednostavan izbor."><?php wp_head(); ?></head><body <?php body_class('lagano-'.$lagano_view.'-page'); ?>><?php echo do_shortcode('[lagano_walks view="'.$lagano_view.'"]'); wp_footer(); ?></body></html>
