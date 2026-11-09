<?php
add_theme_support('title-tag');
remove_action('wp_head','print_emoji_detection_script',7);
remove_action('wp_print_styles','print_emoji_styles');
add_filter('wp_robots',function($r){$r['noindex']=true;$r['nofollow']=true;return $r;});
