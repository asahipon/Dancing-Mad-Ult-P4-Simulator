from direct.showbase.ShowBase import ShowBase
from direct.showbase.Audio3DManager import Audio3DManager
from panda3d.core import (
    WindowProperties, Vec3, Vec2, CardMaker, TransparencyAttrib,
    Geom, GeomNode, GeomVertexFormat, GeomVertexData,
    GeomVertexWriter, GeomTriangles, TextNode, LineSegs, Filename
)
from direct.gui.DirectGui import DirectButton, DirectSlider, DirectFrame, DGG
from direct.gui.OnscreenText import OnscreenText
from direct.task import Task
import math
import os
import random


class SimulatorBase(ShowBase):
    def __init__(self):
        super().__init__()

        # --- ウィンドウ設定 ---
        props = WindowProperties()
        props.setTitle("FF14 Simulator Base")
        win_w, win_h = 1920, 810
        props.setSize(win_w, win_h)
        pipe = self.pipe
        display_w = pipe.getDisplayWidth()
        display_h = pipe.getDisplayHeight()
        pos_x = (display_w - win_w) // 2 - 50
        pos_y = (display_h - win_h) // 2 - 40
        props.setOrigin(pos_x, pos_y)
        self.win.requestProperties(props)
        self.disableMouse()

        # --- 画面分割 ---
        # 左1440px（75%）: シミュレータ描画領域
        # 右 480px（25%）: 設定GUI用領域
        # 元の1600x675から全体を1.2倍し、GUIの比率をそのまま維持する
        self.game_view_ratio = 1440 / 1920

        # ShowBase が作成した既定の3D描画領域を左側だけに制限する
        self.game_display_region = self.camNode.getDisplayRegion(0)
        self.game_display_region.setDimensions(
            0.0, self.game_view_ratio, 0.0, 1.0
        )

        # 右側GUIは、Panda3Dが標準で用意する2D描画領域（aspect2d）を使用する。
        # 独立DisplayRegionを作ってclearすると、描画順によってDirectGUIを
        # 上から消してしまうため、3Dだけ左75%に制限し、GUIはaspect2dに重ねる。

        # --- パラメータ ---
        self.move_speed = 3.6      # 移動速度
        self.camera_yaw = 0        # カメラの向き
        self.camera_pitch = 10.0
        self.camera_dist = 11.0
        self.camera_height = 4.0
        self.field_radius = 12.0

        # --- 叫声デバフ判定円 ---
        # 黄緑色のターゲットサークル半径(field_radius * 0.3)より
        # 一回り小さい円。添付の仕様図に合わせ、およそ75%とする。
        self.shriek_safe_radius = self.field_radius * 0.3 * 0.75

        # --- プレイヤーの向き ---
        # 0°=北(+Y), 90°=東(+X), 180°=南, 270°=西。
        # 0～360°の連続角度で保持する。
        self.player_heading = 0.0
        self.player_is_moving = False

        # --- ギミック用パラメータ ---
        # Excel「パラメータ辞書」に合わせ、pattern 辞書で保持する。
        # 起動時に全項目をランダム生成する。
        self.pattern = {}
        self.generate_pattern()

        # --- 入力状態 ---
        self.keys = {
            "w": False,
            "a": False,
            "s": False,
            "d": False,
            "q": False,
            "e": False,
        }
        for k in self.keys:
            self.accept(k, self.set_key, [k, True])
            self.accept(f"{k}-up", self.set_key, [k, False])

        # --- マウス右ドラッグでカメラ回転 ---
        self.mouse_look_active = False
        self.mouse_sensitivity_x = 1.80
        self.mouse_sensitivity_y = 0.35
        self.last_mouse_x = 0.0
        self.last_mouse_y = 0.0
        self.accept("mouse3", self.on_mouse3_down)
        self.accept("mouse3-up", self.on_mouse3_up)

        # --- シーン ---
        self.setup_scene()

        # --- ケフカ床AoE表示 ---
        # デバッグ用の仮デフォルト：
        # 雷 = NE_SW_N / 氷 = NE
        # ここでは見た目だけを作り、Hit判定はまだ行わない。
        self.setup_kefka_floor_aoe()
        self.set_kefka_floor_aoe(
            thunder_pattern=self.pattern["floor_thunder"][0],
            blizzard_pattern=self.pattern["floor_blizzard"][0],
            thunder_visible=False,
            blizzard_visible=False,
        )

        # --- 敵表示 ---
        self.setup_enemies()

        # --- ケフカ Chariot / Dynamo AoE ---
        # デバッグ確認用として Chariot を表示。
        # タイムライン実装時は
        # show_kefka_radial_aoe("CHARIOT" / "DYNAMO") と
        # hide_kefka_radial_aoe() で切り替える。
        self.setup_kefka_radial_aoe()
        self.hide_kefka_radial_aoe()

        # --- エクスデス左右ビーム ---
        # デバッグ用：起動時は表示。
        # beam_color:
        #   LEFTBLUE  = 向かって左が青、右が紫
        #   RIGHTBLUE = 向かって右が青、左が紫
        self.setup_exdeath_beam_effect()
        self.set_exdeath_beam_color(self.pattern["beam_color"])
        self.show_exdeath_beam_effect()

        # --- エクスデス左右ビーム床 ---
        # デバッグ用：起動時は表示。
        # beam_color と beam_truth の組み合わせから、
        # フィールド左半分 / 右半分の青・紫を決定する。
        self.setup_exdeath_beam_floor()
        self.set_exdeath_beam_floor(
            beam_color=self.pattern["beam_color"],
            beam_truth=self.pattern["beam_truth"],
            visible=True,
        )

        # --- ケフカ真偽エフェクト ---
        # デバッグ用の仮デフォルト：
        # 上段（雷）= 偽、下段（氷）= 真
        # あとでタイムライン側から表示/非表示・真偽を切り替える。
        self.setup_kefka_truth_effect()
        self.set_kefka_truth_effect(
            upper_visible=True,
            lower_visible=True,
            upper_truth=False,
            lower_truth=True,
        )

        # --- カオス真偽エフェクト ---
        # デバッグ用の仮デフォルト：偽
        # 線はなく、真偽の玉2個だけが時計回りに公転する。
        self.setup_chaos_truth_effect()
        self.set_chaos_truth_effect(
            visible=True,
            truth=False,
        )

        # --- エクスデス真偽エフェクト ---
        # デバッグ用の仮デフォルト：偽
        # エクスデス正面の縦円軌道上を、玉2個が反時計回りに公転する。
        # 線そのものは表示しない。
        self.setup_exdeath_truth_effect()
        self.set_exdeath_truth_effect(
            visible=True,
            truth=False,
        )

        # --- 右側GUI ---
        self.setup_gui()

        # --- プレイヤー頭上マーカー ---
        # マクロ・補助機能のどちらからでも呼べるよう、独立した機能として初期化。
        self.setup_player_head_marker()

        # --- ミス瞬間通知 ---
        # ミス発生時、ゲーム画面中央より少し上に「MISS」を短時間表示する。
        self.setup_miss_overlay()

        # --- 発動タイミング視覚エフェクト ---
        # 早/遅デバフ：自キャラ中心の短い白リング
        # 視線：ゲーム画面全体の約0.5秒の白フラッシュ
        self.setup_activation_effects()

        # --- 頭割り判定範囲デバッグ表示 ---
        # 実際の判定と同じ寸法・同じ軸変換を使って半透明オレンジで可視化。
        # デバッグ表示は通常時OFF。必要時は show_stack_area_debug() で表示。
        self.setup_stack_area_debug()
        self.stack_area_debug_visible = False
        self.update_stack_area_debug()

        # --- 補助音声 ---
        # Excel「補助音声一覧」準拠。音声は ./sound に配置する。
        self.setup_supplemental_audio()
        self.setup_positional_audio()

        # --- 敵の詠唱バー ---
        # 詠唱名に gui_font を使用するため、setup_gui() の後で初期化する。
        self.setup_enemy_cast_bars()

        # --- 左側ログ表示 ---
        self.setup_left_log_boxes()

        # --- デバフ表示欄 ---
        # タイムライン実装用の初期状態：
        # デバッグ表示をすべて隠し、ケフカ本体だけにする。
        self.apply_timeline_initial_state()

        # 詠唱バーはタイムライン途中で開始するため、起動時は全て非表示。
        for enemy_id in ("kefka", "chaos", "exdeath"):
            self.stop_enemy_cast(enemy_id, hide=True)

        self.setup_debuff_display()

        # デバフはタイムライン途中で付与するため、起動時は全て非表示。
        # setup_debuff_display() 内で active_debuffs は空の状態で初期化される。
        self.clear_all_debuffs()

        # --- 5x5アクションボタン ---
        self.setup_action_button_grid()

        # --- タイムライン ---
        # Excel「タイムライン」の最終122.3秒までを実装。
        self.setup_timeline()

        # 起動時のミス内容は空欄。
        self.clear_miss_text()

        self.start_timeline()

        # --- 更新処理 ---
        self.taskMgr.add(self.update, "update")

    # NOTE:
    # 最新「デバフ詳細」の付与条件に "shriek_enable" とある箇所は、
    # 「パラメータ辞書」で定義済みの正式key "shriek_enabled" として扱う。

    def generate_pattern(self):
        """
        Excel「パラメータ辞書」準拠で、1回分のpatternをランダム生成する。

        ・配列数1の項目はscalar
        ・配列数2以上の項目はlist
        ・他の値から一意に決まるものはpatternに置かず、
          generate_derived() で derived に生成する
        """
        bool_choice = lambda: random.choice([True, False])

        self.pattern = {
            "exdeath_truth": [bool_choice() for _ in range(3)],
            "chaos_truth": [bool_choice() for _ in range(2)],
            "chaos_fire": random.choice(["EARLY", "LATE"]),
            "beam_truth": bool_choice(),
            "beam_color": random.choice(["LEFTBLUE", "RIGHTBLUE"]),
            "exdeath_direction": random.choice(
                ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
            ),

            # GC1 / GC2 / GC3 / Magic Charge / Magic Out
            "kefka_thunder": [bool_choice() for _ in range(5)],
            "kefka_blizzard": [bool_choice() for _ in range(5)],

            # GC1 / GC2 / GC3 / Magic Charge / Magic Out
            "floor_thunder": [
                random.choice(
                    ["NE_SW_N", "NE_SW_S", "NW_SE_N", "NW_SE_S"]
                )
                for _ in range(5)
            ],
            "floor_blizzard": [
                random.choice(["NE", "NW"])
                for _ in range(5)
            ],

            # 個人デバフ：付与GCと発動時間は独立
            "water_light_type": random.choice(["WATER", "LIGHT"]),
            "water_light_time": random.choice(["EARLY", "LATE"]),
            "water_light_gc": random.choice([0, 1]),

            # 加速度：発動時間だけ独立乱数。
            # accel_gc は water_light_gc から derived で決める。
            "accel_time": random.choice(["EARLY", "LATE"]),

            # 叫声：付与GCだけをpatternに持つ。
            # shriek_time は shriek_gc から derived で決める。
            "shriek_enabled": bool_choice(),
            "shriek_gc": random.choice([0, 1]),

            "wound_type": random.choice(["DEATH", "ALLAGAN"]),
            "wound_color": random.choice(["BLUE", "PURPLE"]),
        }

        self.generate_derived()

    def generate_derived(self):
        """
        Excel「パラメータ辞書派生」準拠。
        patternから一意に決まる値を self.derived にまとめる。
        """
        p = self.pattern

        # 叫声発動時間
        shriek_time = "EARLY" if p["shriek_gc"] == 0 else "LATE"

        # 個人デバフの付与GCと背反
        accel_gc = 1 - p["water_light_gc"]

        # 個人デバフ：STACK / SPREAD_EARLY / SPREAD_LATE
        #
        # 参照truth = exdeath_truth[water_light_gc]
        #
        # TRUE + WATER  -> STACK
        # TRUE + LIGHT  -> water_light_time に応じて SPREAD_EARLY / SPREAD_LATE
        # FALSE + WATER -> water_light_time に応じて SPREAD_EARLY / SPREAD_LATE
        # FALSE + LIGHT -> STACK
        water_light_truth = p["exdeath_truth"][p["water_light_gc"]]

        if (
            (water_light_truth and p["water_light_type"] == "WATER")
            or
            ((not water_light_truth) and p["water_light_type"] == "LIGHT")
        ):
            water_light_result = "STACK"
        else:
            water_light_result = (
                "SPREAD_EARLY"
                if p["water_light_time"] == "EARLY"
                else "SPREAD_LATE"
            )

        # 加速度：STOP / MOVE
        accel_truth = p["exdeath_truth"][accel_gc]
        accel_result = "STOP" if accel_truth else "MOVE"

        # ほのお / つなみ：chaos_fire に応じて参照truthが入れ替わる
        if p["chaos_fire"] == "EARLY":
            fire_truth = p["chaos_truth"][0]
            tsunami_truth = p["chaos_truth"][1]
        else:
            fire_truth = p["chaos_truth"][1]
            tsunami_truth = p["chaos_truth"][0]

        fire_result = "CHARIOT" if fire_truth else "DYNAMO"
        tsunami_result = "DYNAMO" if tsunami_truth else "CHARIOT"

        # 傷：DEATHは同色、ALLAGANは異色
        if p["wound_type"] == "DEATH":
            wound_result = p["wound_color"]
        else:
            wound_result = (
                "PURPLE" if p["wound_color"] == "BLUE" else "BLUE"
            )

        # ビーム床の最終左右色。
        # beam_truth=False の場合は見た目の左右色を反転する。
        if p["beam_truth"]:
            beam_color_result = p["beam_color"]
        else:
            beam_color_result = (
                "RIGHTBLUE"
                if p["beam_color"] == "LEFTBLUE"
                else "LEFTBLUE"
            )

        # Magic Out 最終真偽
        # Magic Charge 時点（index=3）と Magic Out 時点（index=4）の
        # 真偽が一致していれば最終的に「真」となる。
        magic_out_thunder_truth = (
            p["kefka_thunder"][3] == p["kefka_thunder"][4]
        )
        magic_out_blizzard_truth = (
            p["kefka_blizzard"][3] == p["kefka_blizzard"][4]
        )

        # GC床コール用。n=0,1,2
        kefka_tb_result = []
        for n in range(3):
            blizzard_truth = p["kefka_blizzard"][n]
            thunder_truth = p["kefka_thunder"][n]

            if blizzard_truth and thunder_truth:
                result = "NONE"
            elif blizzard_truth and not thunder_truth:
                result = "THUNDER"
            elif not blizzard_truth and thunder_truth:
                result = "BLIZZARD"
            else:
                result = "BOTH"

            kefka_tb_result.append(result)

        # Magic Out床コール用
        if magic_out_blizzard_truth and magic_out_thunder_truth:
            magic_out_result = "NONE"
        elif magic_out_blizzard_truth and not magic_out_thunder_truth:
            magic_out_result = "THUNDER"
        elif not magic_out_blizzard_truth and magic_out_thunder_truth:
            magic_out_result = "BLIZZARD"
        else:
            magic_out_result = "BOTH"

        self.derived = {
            "shriek_time": shriek_time,
            "accel_gc": accel_gc,
            "water_light_result": water_light_result,
            "accel_result": accel_result,
            "fire_result": fire_result,
            "tsunami_result": tsunami_result,
            "wound_result": wound_result,
            "beam_color_result": beam_color_result,
            "magic_out_thunder_truth": magic_out_thunder_truth,
            "magic_out_blizzard_truth": magic_out_blizzard_truth,
            "kefka_tb_result": kefka_tb_result,
            "magic_out_result": magic_out_result,
        }

    def setup_timeline(self):
        """
        Excel「タイムライン」の最終122.3secまでをイベント化する。

        時刻は小数第1位までを仕様値として使用する。
        関数名は「秒×10」を4桁ゼロ埋めで統一する。

        デバフ付与は、タイムラインの「イベント：○○」と
        「デバフ詳細」の同名イベントを対応させる。
        """
        self.timeline_events = [
            (0.1, self._timeline_0001_kefka_cast),
            (5.5, self._timeline_0055_show_enemies),

            (9.7, self._timeline_0097_kefka_cast),
            (9.7, self._timeline_0097_kefka_truth),
            (9.7, self._timeline_0097_kefka_floor),
            (10.1, self._timeline_0101_exdeath_cast),
            (10.1, self._timeline_0101_exdeath_truth),
            (10.6, self._timeline_0106_gc_and_exdeath_call),
            (11.1, self._timeline_0111_gaze_chat),

            (15.1, self._timeline_0151_kefka_floor_end),
            (15.2, self._timeline_0152_chaos_truth),
            (15.2, self._timeline_0152_chaos_cast),
            (16.2, self._timeline_0162_fire_tsunami_chat),

            (19.1, self._timeline_0191_first_gc_debuffs),
            (20.7, self._timeline_0207_exdeath_truth_end),
            (22.1, self._timeline_0221_personal_result_echo),
            (22.1, self._timeline_0221_accel_result_echo),

            (24.6, self._timeline_0246_kefka_cast),
            (24.6, self._timeline_0246_kefka_truth),
            (24.6, self._timeline_0246_kefka_floor),
            (24.7, self._timeline_0247_first_fire_tsunami_debuff),

            (25.1, self._timeline_0251_exdeath_cast),
            (25.1, self._timeline_0251_exdeath_truth),
            (25.2, self._timeline_0252_chaos_truth_end),
            (25.6, self._timeline_0256_gc_and_exdeath_call),
            (26.1, self._timeline_0261_gaze_chat),

            (30.0, self._timeline_0300_kefka_floor_end),
            (30.2, self._timeline_0302_chaos_truth),
            (30.2, self._timeline_0302_chaos_cast),
            (31.2, self._timeline_0312_fire_tsunami_chat),

            (34.1, self._timeline_0341_second_gc_debuffs),
            (35.7, self._timeline_0357_exdeath_truth_end),
            (37.1, self._timeline_0371_personal_result_echo),
            (37.1, self._timeline_0371_accel_result_echo),

            # ---------- 3回目 ----------
            (39.8, self._timeline_0398_kefka_cast),
            (39.8, self._timeline_0398_kefka_truth),
            (39.8, self._timeline_0398_kefka_floor),

            (40.2, self._timeline_0402_exdeath_cast),
            (40.2, self._timeline_0402_exdeath_truth),
            (40.3, self._timeline_0403_chaos_truth_end),
            (40.7, self._timeline_0407_gc_floor_call),

            (40.9, self._timeline_0409_second_fire_tsunami_debuff),
            (42.6, self._timeline_0426_hide_chaos),

            (45.2, self._timeline_0452_kefka_floor_end),

            (50.5, self._timeline_0505_third_gc_debuffs),
            (50.8, self._timeline_0508_exdeath_truth_end),

            # ---------- ビーム ----------
            (53.1, self._timeline_0531_hide_exdeath),
            (54.3, self._timeline_0543_exdeath_move_sound),
            (54.8, self._timeline_0548_show_exdeath),
            (56.2, self._timeline_0562_exdeath_void_cast),
            (56.2, self._timeline_0562_beam_truth),
            (56.2, self._timeline_0562_beam_cards),
            (56.7, self._timeline_0567_beam_truth_call),

            (61.8, self._timeline_0618_beam_floor),
            (61.8, self._timeline_0618_beam_hit_check),

            (63.2, self._timeline_0632_beam_floor_end),
            (65.5, self._timeline_0655_hide_exdeath),

            # ---------- マジックチャージ～早デバフ ----------
            (66.9, self._timeline_0669_magic_charge_cast),

            (70.1, self._timeline_0701_early_activation_effect),
            (70.1, self._timeline_0701_early_personal_check),
            (70.1, self._timeline_0701_early_accel_check),

            # 早視線の発動時コールをMC雷から分離し、大きく前倒し
            (71.6, self._timeline_0716_early_gaze_call),

            (73.1, self._timeline_0731_kefka_thunder_cast),
            (73.1, self._timeline_0731_kefka_thunder_truth),
            (73.1, self._timeline_0731_kefka_thunder_floor),

            (74.1, self._timeline_0741_magic_charge_thunder_call),
            (74.1, self._timeline_0741_magic_charge_chat),

            (78.5, self._timeline_0785_kefka_thunder_end),

            (79.1, self._timeline_0791_gaze_activation_effect),
            (79.1, self._timeline_0791_early_gaze_check),
            (79.1, self._timeline_0791_early_shriek_position_check),

            # ---------- 炎～MC氷 ----------
            (83.3, self._timeline_0833_kefka_ultima_cast),
            (84.3, self._timeline_0843_fire_result_call),
            (85.9, self._timeline_0859_fire_place_check),

            (91.0, self._timeline_0910_fire_radial_aoe),
            (91.0, self._timeline_0910_fire_radial_check),

            (91.3, self._timeline_0913_kefka_blizzard_cast),
            (91.3, self._timeline_0913_kefka_blizzard_truth),
            (91.3, self._timeline_0913_kefka_blizzard_floor),

            (91.4, self._timeline_0914_hide_radial_aoe),

            (92.3, self._timeline_0923_magic_charge_blizzard_call),
            (92.3, self._timeline_0923_magic_charge_blizzard_chat),

            # ---------- 遅デバフ ----------
            (95.1, self._timeline_0951_late_activation_effect),
            (95.1, self._timeline_0951_late_personal_check),
            (95.1, self._timeline_0951_late_accel_check),

            (96.7, self._timeline_0967_kefka_blizzard_end),

            # 遅視線の発動時コール
            (98.2, self._timeline_0982_late_gaze_call),

            # ---------- Magic Out ----------
            (102.5, self._timeline_1025_magic_out_cast),
            (102.5, self._timeline_1025_magic_out_truth),

            (103.1, self._timeline_1031_gaze_activation_effect),
            (103.1, self._timeline_1031_late_gaze_check),
            (103.1, self._timeline_1031_late_shriek_position_check),

            (106.1, self._timeline_1061_tsunami_then_magic_out_call),

            (107.7, self._timeline_1077_magic_out_truth_end),

            (108.7, self._timeline_1087_tsunami_place_check),

            (109.5, self._timeline_1095_magic_out_floor),

            (113.8, self._timeline_1138_tsunami_radial_aoe),
            (113.8, self._timeline_1138_tsunami_radial_check),

            # 判定は114.2秒、見た目は115.2秒まで残す
            (114.2, self._timeline_1142_magic_out_floor_check),

            (115.2, self._timeline_1152_hide_radial_aoe),
            (115.2, self._timeline_1152_magic_out_floor_end),

            # ---------- 最終 ----------
            (122.3, self._timeline_1223_kefka_ultima_cast),
        ]

        self.timeline_time = 0.0
        self.timeline_next_index = 0
        self.timeline_running = False

    def start_timeline(self):
        """0秒からタイムラインを開始する。"""
        self.timeline_time = 0.0
        self.timeline_next_index = 0
        self.timeline_running = True

        # 0秒イベントは起動/Reset直後に即時実行。
        self._run_due_timeline_events()

    def _run_due_timeline_events(self):
        while self.timeline_next_index < len(self.timeline_events):
            event_time, callback = self.timeline_events[self.timeline_next_index]

            if event_time > self.timeline_time + 1e-6:
                break

            print(
                f"[timeline] t={self.timeline_time:.3f} "
                f"event={callback.__name__}"
            )
            callback()
            self.timeline_next_index += 1

    def update_timeline(self, dt):
        if not self.timeline_running:
            return

        self.timeline_time += dt
        self._run_due_timeline_events()

        if self.timeline_next_index >= len(self.timeline_events):
            # 今回実装した122.3秒（Excel最終行）地点までは完了。
            # 後続を追加するため、時間自体は止めずrunningのままでもよいが、
            # 現段階では不要な処理を避けるため停止する。
            self.timeline_running = False

    # ---------------------------
    # タイムライン各イベント
    # ---------------------------
    def _timeline_0001_kefka_cast(self):
        self.start_enemy_cast(
            "kefka",
            "おちょくりソウル",
            4.7,
        )

    def _timeline_0055_show_enemies(self):
        # 最新Excelの列順・基本配置：
        # 向かって左 = カオス（北西）
        # 向かって右 = エクスデス（北東）

        # カオス：setup_enemies() で設定済みの北西位置のまま出現
        self.show_enemy("chaos")

        # エクスデス：北東に出現
        self.set_exdeath_direction("NE")
        self.show_enemy("exdeath")

    def _timeline_0097_kefka_cast(self):
        self.start_enemy_cast(
            "kefka",
            "なぞなぞマジック",
            4.7,
        )

    def _timeline_0097_kefka_truth(self):
        self.set_kefka_truth_effect(
            upper_visible=True,
            lower_visible=True,
            upper_truth=self.pattern["kefka_thunder"][0],
            lower_truth=self.pattern["kefka_blizzard"][0],
        )

    def _validate_kefka_floor_pattern(self, thunder_pattern, blizzard_pattern):
        """タイムラインから床を出す直前にパラメータ値を検証する。"""
        valid_thunder = {
            "NE_SW_N",
            "NE_SW_S",
            "NW_SE_N",
            "NW_SE_S",
        }
        valid_blizzard = {"NE", "NW"}

        if thunder_pattern not in valid_thunder:
            raise ValueError(
                f"Invalid floor_thunder value: {thunder_pattern!r}"
            )
        if blizzard_pattern not in valid_blizzard:
            raise ValueError(
                f"Invalid floor_blizzard value: {blizzard_pattern!r}"
            )

    def _timeline_0097_kefka_floor(self):
        thunder_pattern = self.pattern["floor_thunder"][0]
        blizzard_pattern = self.pattern["floor_blizzard"][0]

        self._validate_kefka_floor_pattern(
            thunder_pattern,
            blizzard_pattern,
        )

        self.set_kefka_floor_aoe(
            thunder_pattern=thunder_pattern,
            blizzard_pattern=blizzard_pattern,
            thunder_visible=True,
            blizzard_visible=True,
        )

    def _timeline_0101_exdeath_cast(self):
        self.start_enemy_cast(
            "exdeath",
            "グランドクロス",
            8.7,
        )

    def _timeline_0101_exdeath_truth(self):
        self.set_exdeath_truth_effect(
            visible=True,
            truth=self.pattern["exdeath_truth"][0],
        )

    def _timeline_0106_gc_and_exdeath_call(self):
        """
        Excel「タイムライン」10.62sec：
        kefka_tb_result[0] のGC床コール枠
        → 音声相当時間の終了後 1.5sec 空白
        → exdeath_truth[0] のエクスデス真偽コール枠

        GUIオプションは各コール枠で音を出すかどうかだけを決める。
        OFFでもコール枠（時間）は保持する。
        """
        self.play_gc_floor_then_exdeath_call(
            floor_result=self.derived["kefka_tb_result"][0],
            exdeath_truth=self.pattern["exdeath_truth"][0],
            gap=1.5,
        )

    def _timeline_0111_gaze_chat(self):
        """
        Excel「タイムライン」11.12sec。

        exdeath_truth[0] に応じてメッセージを決め、
        チャット補助「視線」(chat_gaze) がONのときだけ
        PTチャット欄へ追記する。
        """
        truth = bool(self.pattern["exdeath_truth"][0])

        if truth:
            message = "視線１：見ない（本当）"
        else:
            message = "視線１：見る（ウソ）"

        self.append_pt_chat_assist(
            "chat_gaze",
            message,
        )

    def _timeline_0151_kefka_floor_end(self):
        # AoEを消す前に、14.4秒時点の立ち位置を判定する。
        self.check_kefka_floor_position(index=0)

        # 雷床・氷床を非表示
        self.hide_kefka_floor_aoe()

        # ケフカの雷/氷 真偽エフェクトも同時に非表示
        self.hide_kefka_truth_effect()

    def _timeline_0152_chaos_cast(self):
        cast_name = (
            "ほのお"
            if self.pattern["chaos_fire"] == "EARLY"
            else "つなみ"
        )

        self.start_enemy_cast(
            "chaos",
            cast_name,
            8.7,
        )

    def _timeline_0152_chaos_truth(self):
        self.set_chaos_truth_effect(
            visible=True,
            truth=self.pattern["chaos_truth"][0],
        )

    def _timeline_0162_fire_tsunami_chat(self):
        """
        Excel「タイムライン」16sec：
        チャット補助「炎つなみ」がONのときPTチャット欄へ追記。
        """
        if self.pattern["chaos_fire"] == "EARLY":
            if self.derived["fire_result"] == "DYNAMO":
                message = "炎：中（ドーナツ）"
            else:
                message = "炎：外（たけのこ）"
        else:
            if self.derived["tsunami_result"] == "DYNAMO":
                message = "つなみ：中（ドーナツ）"
            else:
                message = "つなみ：外（たけのこ）"

        self.append_pt_chat_assist(
            "chat_fire_tsunami",
            message,
        )

    def _timeline_0191_first_gc_debuffs(self):
        """
        Excel「タイムライン」19.09sec
        イベント：1回目GC

        「デバフ詳細」の同名イベントを参照して付与する。

        ・water_light_gc = 0
            -> water_light_type / water_light_time に応じて
               water.png または light.png、51/76秒

        ・accel_gc = 0
            -> accel_time に応じて accel.png、51/76秒

        ・shriek_enabled = True かつ shriek_gc = 0
            -> shriek.png、60秒
        """
        # --- 個人デバフ ---
        if self.pattern["water_light_gc"] == 0:
            filename = (
                "water.png"
                if self.pattern["water_light_type"] == "WATER"
                else "light.png"
            )
            countdown = (
                51
                if self.pattern["water_light_time"] == "EARLY"
                else 76
            )
            self.add_debuff(
                "water_light",
                filename=filename,
                countdown=countdown,
            )

        # --- 加速度 ---
        if self.derived["accel_gc"] == 0:
            countdown = (
                51
                if self.pattern["accel_time"] == "EARLY"
                else 76
            )
            self.add_debuff(
                "accel",
                filename="accel.png",
                countdown=countdown,
            )

        # --- 叫声 ---
        if (
            self.pattern["shriek_enabled"]
            and self.pattern["shriek_gc"] == 0
        ):
            self.add_debuff(
                "shriek",
                filename="shriek.png",
                countdown=60,
            )

    def _timeline_0221_personal_result_echo(self):
        """
        Excel「タイムライン」22.1sec：
        個人デバフ補助「散会、頭割り」がON、
        かつ water_light_gc=0 のとき、water_light_result に応じて
        3行目の散会マクロを自動実行する。

        SPREAD_EARLY -> (3, 1)
            「早散会（ビーム受けたまま）」をエコー表示 + 頭上1マーク
        SPREAD_LATE -> (4, 2)
            「遅散会（東西）」をエコー表示 + 頭上2マーク
        STACK -> 何もしない
        """
        if not self.gui_flags.get("personal_spread_stack", False):
            return

        if self.pattern["water_light_gc"] != 0:
            return

        result = self.derived["water_light_result"]

        if result == "SPREAD_EARLY":
            self.execute_macro(3, 1)
        elif result == "SPREAD_LATE":
            self.execute_macro(4, 2)

    def _timeline_0221_accel_result_echo(self):
        """
        Excel「タイムライン」22.1sec：
        個人デバフ補助「加速度」がON、
        かつ accel_gc=0 のときエコー欄へ表示。
        """
        if not self.gui_flags.get("personal_acceleration", False):
            return

        if self.derived["accel_gc"] != 0:
            return

        if self.derived["accel_result"] == "STOP":
            message = "◎動かない"
        else:
            message = "？動く"

        self.append_echo_text(message)

    def _timeline_0207_exdeath_truth_end(self):
        """Excel「タイムライン」20.72sec：エクスデス真偽エフェクト非表示。"""
        self.hide_exdeath_truth_effect()

    # ---------------------------
    # 24.62～35.68秒：2回目
    # ---------------------------
    def _timeline_0246_kefka_cast(self):
        self.start_enemy_cast(
            "kefka",
            "なぞなぞマジック",
            4.7,
        )

    def _timeline_0246_kefka_truth(self):
        self.set_kefka_truth_effect(
            upper_visible=True,
            lower_visible=True,
            upper_truth=self.pattern["kefka_thunder"][1],
            lower_truth=self.pattern["kefka_blizzard"][1],
        )

    def _timeline_0246_kefka_floor(self):
        thunder_pattern = self.pattern["floor_thunder"][1]
        blizzard_pattern = self.pattern["floor_blizzard"][1]

        self._validate_kefka_floor_pattern(
            thunder_pattern,
            blizzard_pattern,
        )

        self.set_kefka_floor_aoe(
            thunder_pattern=thunder_pattern,
            blizzard_pattern=blizzard_pattern,
            thunder_visible=True,
            blizzard_visible=True,
        )

    def _timeline_0247_first_fire_tsunami_debuff(self):
        """
        Excel「デバフ詳細」
        イベント：1回目ほのおつなみ

        chaos_fire = EARLY -> fire.png / 61秒
        chaos_fire = LATE  -> tsunami.png / 84秒
        """
        if self.pattern["chaos_fire"] == "EARLY":
            self.add_debuff(
                "fire",
                filename="fire.png",
                countdown=61,
            )
        else:
            self.add_debuff(
                "tsunami",
                filename="tsunami.png",
                countdown=84,
            )

    def _timeline_0251_exdeath_cast(self):
        self.start_enemy_cast(
            "exdeath",
            "グランドクロス",
            8.7,
        )

    def _timeline_0251_exdeath_truth(self):
        self.set_exdeath_truth_effect(
            visible=True,
            truth=self.pattern["exdeath_truth"][1],
        )

    def _timeline_0252_chaos_truth_end(self):
        self.hide_chaos_truth_effect()

    def _timeline_0256_gc_and_exdeath_call(self):
        self.play_gc_floor_then_exdeath_call(
            floor_result=self.derived["kefka_tb_result"][1],
            exdeath_truth=self.pattern["exdeath_truth"][1],
            gap=1.5,
        )

    def _timeline_0261_gaze_chat(self):
        truth = bool(self.pattern["exdeath_truth"][1])

        if truth:
            message = "視線２：見ない（本当）"
        else:
            message = "視線２：見る（ウソ）"

        self.append_pt_chat_assist(
            "chat_gaze",
            message,
        )

    def _timeline_0300_kefka_floor_end(self):
        # 2回目（配列index=1）の床で判定する。
        self.check_kefka_floor_position(index=1)

        self.hide_kefka_floor_aoe()
        self.hide_kefka_truth_effect()

    def _timeline_0302_chaos_truth(self):
        self.set_chaos_truth_effect(
            visible=True,
            truth=self.pattern["chaos_truth"][1],
        )

    def _timeline_0302_chaos_cast(self):
        # 2回目は1回目と逆。
        cast_name = (
            "つなみ"
            if self.pattern["chaos_fire"] == "EARLY"
            else "ほのお"
        )

        self.start_enemy_cast(
            "chaos",
            cast_name,
            8.7,
        )

    def _timeline_0312_fire_tsunami_chat(self):
        """
        2回目はchaos_fireに対する炎/つなみの対応が1回目と逆。
        """
        if self.pattern["chaos_fire"] == "EARLY":
            if self.derived["tsunami_result"] == "DYNAMO":
                message = "つなみ：中（ドーナツ）"
            else:
                message = "つなみ：外（たけのこ）"
        else:
            if self.derived["fire_result"] == "DYNAMO":
                message = "炎：中（ドーナツ）"
            else:
                message = "炎：外（たけのこ）"

        self.append_pt_chat_assist(
            "chat_fire_tsunami",
            message,
        )

    def _timeline_0341_second_gc_debuffs(self):
        """
        Excel「デバフ詳細」
        イベント：2回目GC

        ・water_light_gc = 1
            water/light、36秒または61秒
        ・accel_gc = 1
            accel、36秒または61秒
        ・shriek_enabled=True かつ shriek_gc=1
            shriek、69秒
        """
        if self.pattern["water_light_gc"] == 1:
            filename = (
                "water.png"
                if self.pattern["water_light_type"] == "WATER"
                else "light.png"
            )
            countdown = (
                36
                if self.pattern["water_light_time"] == "EARLY"
                else 61
            )
            self.add_debuff(
                "water_light",
                filename=filename,
                countdown=countdown,
            )

        if self.derived["accel_gc"] == 1:
            countdown = (
                36
                if self.pattern["accel_time"] == "EARLY"
                else 61
            )
            self.add_debuff(
                "accel",
                filename="accel.png",
                countdown=countdown,
            )

        if (
            self.pattern["shriek_enabled"]
            and self.pattern["shriek_gc"] == 1
        ):
            self.add_debuff(
                "shriek",
                filename="shriek.png",
                countdown=69,
            )

    def _timeline_0371_personal_result_echo(self):
        """
        Excel「タイムライン」37.1sec：
        個人デバフ補助「散会、頭割り」がON、
        かつ water_light_gc=1 のとき、water_light_result に応じて
        4行目の散会マクロを自動実行する。

        SPREAD_EARLY -> (3, 1)
            「早散会（ビーム受けたまま）」をエコー表示 + 頭上1マーク
        SPREAD_LATE -> (4, 2)
            「遅散会（東西）」をエコー表示 + 頭上2マーク
        STACK -> 何もしない
        """
        if not self.gui_flags.get("personal_spread_stack", False):
            return

        if self.pattern["water_light_gc"] != 1:
            return

        result = self.derived["water_light_result"]

        if result == "SPREAD_EARLY":
            self.execute_macro(3, 1)
        elif result == "SPREAD_LATE":
            self.execute_macro(4, 2)

    def _timeline_0371_accel_result_echo(self):
        if not self.gui_flags.get("personal_acceleration", False):
            return

        if self.derived["accel_gc"] != 1:
            return

        if self.derived["accel_result"] == "STOP":
            message = "◎動かない"
        else:
            message = "？動く"

        self.append_echo_text(message)

    def _timeline_0357_exdeath_truth_end(self):
        self.hide_exdeath_truth_effect()

    # ---------------------------
    # 15.06秒 ケフカ雷/氷床の位置判定
    # ---------------------------
    def _player_is_on_thunder_visual(self, index=0):
        """現在のプレイヤー位置が指定回の雷床の見た目上にあるか。"""
        x = self.player.getX()
        y = self.player.getY()

        pattern = self.pattern["floor_thunder"][index]
        inv_sqrt2 = 1.0 / math.sqrt(2.0)

        if pattern.startswith("NE_SW"):
            # 帯に垂直な方向：NW/SE
            normal = Vec2(-inv_sqrt2, inv_sqrt2)
        else:
            # 帯に垂直な方向：NE/SW
            normal = Vec2(inv_sqrt2, inv_sqrt2)

        side = pattern.rsplit("_", 1)[1]

        if side == "N":
            offsets = (
                self.thunder_outer_offset,
                -self.thunder_inner_offset,
            )
        else:
            offsets = (
                self.thunder_inner_offset,
                -self.thunder_outer_offset,
            )

        # normal方向への射影値。
        projection = x * normal.x + y * normal.y
        half_width = self.thunder_band_width / 2.0

        return any(
            abs(projection - offset) <= half_width
            for offset in offsets
        )

    def _player_is_on_blizzard_visual(self, index=0):
        """現在のプレイヤー位置が指定回の氷床の見た目上にあるか。"""
        x = self.player.getX()
        y = self.player.getY()

        pattern = self.pattern["floor_blizzard"][index]

        if pattern == "NE":
            # NE + SW
            return (x >= 0 and y >= 0) or (x <= 0 and y <= 0)

        # NW + SE
        return (x <= 0 and y >= 0) or (x >= 0 and y <= 0)

    def check_kefka_floor_position(self, index=0):
        """
        指定回の雷/氷床について正誤判定する。

        index=0 : 1回目
        index=1 : 2回目

        Excel仕様：
        true  -> 見た目のAoEが危険なので「表示がない場所」が正解
        false -> 反転するため「表示がある場所」が正解
        """
        on_thunder = self._player_is_on_thunder_visual(index)
        on_blizzard = self._player_is_on_blizzard_visual(index)

        thunder_truth = self.pattern["kefka_thunder"][index]
        blizzard_truth = self.pattern["kefka_blizzard"][index]

        thunder_correct = (
            (not on_thunder)
            if thunder_truth
            else on_thunder
        )
        blizzard_correct = (
            (not on_blizzard)
            if blizzard_truth
            else on_blizzard
        )

        if not (thunder_correct and blizzard_correct):
            self.set_miss_text("雷/氷床を踏んだ")

    # ---------------------------
    # 39.8～50.8秒：3回目
    # ---------------------------
    def _timeline_0398_kefka_cast(self):
        self.start_enemy_cast(
            "kefka",
            "なぞなぞマジック",
            4.7,
        )

    def _timeline_0398_kefka_truth(self):
        self.set_kefka_truth_effect(
            upper_visible=True,
            lower_visible=True,
            upper_truth=self.pattern["kefka_thunder"][2],
            lower_truth=self.pattern["kefka_blizzard"][2],
        )

    def _timeline_0398_kefka_floor(self):
        thunder_pattern = self.pattern["floor_thunder"][2]
        blizzard_pattern = self.pattern["floor_blizzard"][2]

        self._validate_kefka_floor_pattern(
            thunder_pattern,
            blizzard_pattern,
        )

        self.set_kefka_floor_aoe(
            thunder_pattern=thunder_pattern,
            blizzard_pattern=blizzard_pattern,
            thunder_visible=True,
            blizzard_visible=True,
        )

    def _timeline_0402_exdeath_cast(self):
        self.start_enemy_cast(
            "exdeath",
            "グランドクロス",
            8.7,
        )

    def _timeline_0402_exdeath_truth(self):
        self.set_exdeath_truth_effect(
            visible=True,
            truth=self.pattern["exdeath_truth"][2],
        )

    def _timeline_0403_chaos_truth_end(self):
        self.hide_chaos_truth_effect()

    def _timeline_0407_gc_floor_call(self):
        self.play_gc_floor_call_slot(
            self.derived["kefka_tb_result"][2]
        )

    def _timeline_0409_second_fire_tsunami_debuff(self):
        """
        Excel「デバフ詳細」
        イベント：2回目ほのおつなみ
        """
        if self.pattern["chaos_fire"] == "EARLY":
            self.add_debuff(
                "tsunami",
                filename="tsunami.png",
                countdown=68,
            )
        else:
            self.add_debuff(
                "fire",
                filename="fire.png",
                countdown=45,
            )

    def _timeline_0426_hide_chaos(self):
        self.hide_chaos_truth_effect()
        self.hide_enemy("chaos")

    def _timeline_0452_kefka_floor_end(self):
        self.check_kefka_floor_position(index=2)
        self.hide_kefka_floor_aoe()
        self.hide_kefka_truth_effect()

    def _timeline_0505_third_gc_debuffs(self):
        """
        Excel「デバフ詳細」
        イベント：3回目GC

        傷デバフ2種を同時付与。
        """
        wound_filename = (
            "death.png"
            if self.pattern["wound_type"] == "DEATH"
            else "allagan.png"
        )
        self.add_debuff(
            "wound_type",
            filename=wound_filename,
            countdown=15,
        )

        color_filename = (
            "blue.png"
            if self.pattern["wound_color"] == "BLUE"
            else "purple.png"
        )
        self.add_debuff(
            "wound_color",
            filename=color_filename,
            countdown=None,
        )

    def _timeline_0508_exdeath_truth_end(self):
        self.hide_exdeath_truth_effect()

    def get_player_beam_floor_side(self):
        """
        エクスデス方向を正面としたビーム床上で、
        プレイヤーが LEFT / RIGHT / CENTER のどこにいるか返す。

        CENTER は中央赤線上で、常に失敗扱い。
        """
        player_world = self.player.getPos(self.render)
        local_pos = self.exdeath_beam_floor_root.getRelativePoint(
            self.render,
            player_world,
        )

        half_line_width = (
            self.exdeath_beam_floor_params["center_line_width"] / 2.0
        )

        if abs(local_pos.x) <= half_line_width:
            return "CENTER"

        return "LEFT" if local_pos.x < 0 else "RIGHT"

    def check_exdeath_beam_position(self):
        """
        61.8secのビーム受け判定。

        wound_result に対応した色の床に立てていれば正解。
        反対色、または中央赤線上なら
        「ビーム受け失敗」をミス欄へ追記する。
        """
        side = self.get_player_beam_floor_side()

        if side == "CENTER":
            self.set_miss_text("ビーム受け失敗")
            return False

        beam_result = self.derived["beam_color_result"]
        wanted_color = self.derived["wound_result"]

        if beam_result == "LEFTBLUE":
            side_color = "BLUE" if side == "LEFT" else "PURPLE"
        else:
            side_color = "PURPLE" if side == "LEFT" else "BLUE"

        if side_color != wanted_color:
            self.set_miss_text("ビーム受け失敗")
            return False

        return True

    # ---------------------------
    # 53.1～65.5秒：エクスデス再登場～ビーム
    # ---------------------------
    def _timeline_0531_hide_exdeath(self):
        self.hide_enemy("exdeath")

    def _timeline_0543_exdeath_move_sound(self):
        # 非表示のまま先にランダム方角へ移動し、
        # その位置から再登場SEを3D再生する。
        self.set_exdeath_direction(
            self.pattern["exdeath_direction"]
        )
        self.play_exdeath_move_sound()

    def _timeline_0548_show_exdeath(self):
        self.show_enemy("exdeath")

    def _timeline_0562_exdeath_void_cast(self):
        self.start_enemy_cast(
            "exdeath",
            "無の氾濫",
            5.3,
        )

    def _timeline_0562_beam_truth(self):
        self.set_exdeath_truth_effect(
            visible=True,
            truth=self.pattern["beam_truth"],
        )

    def _timeline_0562_beam_cards(self):
        self.set_exdeath_beam_color(
            self.pattern["beam_color"]
        )
        self.show_exdeath_beam_effect()

    def _timeline_0567_beam_truth_call(self):
        """
        「ビーム真偽」チェックONなら、
        beam_truth に応じて beam_true/false.wav を再生。
        OFFならこのコール枠は無音。
        """
        if not self.gui_flags.get("call_beam_truth", False):
            return

        filename = self.supplemental_sound_files[
            "beam_truth"
        ][bool(self.pattern["beam_truth"])]

        self._play_supplemental_sound(filename)

    def _timeline_0618_beam_floor(self):
        # derivedで最終的に確定した左右色をそのまま床へ表示。
        self.set_exdeath_beam_floor_result(
            self.derived["beam_color_result"],
            visible=True,
        )

    def _timeline_0618_beam_hit_check(self):
        self.check_exdeath_beam_position()

    def _timeline_0632_beam_floor_end(self):
        self.hide_exdeath_beam_floor()

    def _timeline_0655_hide_exdeath(self):
        self.hide_enemy("exdeath")

    # ---------------------------
    # 66.9～83.3秒：マジックチャージ～早デバフ
    # ---------------------------
    def _timeline_0669_magic_charge_cast(self):
        self.start_enemy_cast(
            "kefka",
            "マジックチャージ",
            2.7,
        )

    # ---------------------------
    # 頭割り / 散会判定
    # ---------------------------
    STACK_HALF_WIDTH = 2.20
    STACK_INNER_DISTANCE = 1.40
    STACK_OUTER_DISTANCE = 8.40

    def get_stack_axis_from_exdeath_direction(self, direction):
        """
        exdeath_direction の8方向を、頭割り判定の4軸へ変換する。

        N/S   -> N_S    (AC)
        NE/SW -> NE_SW  (24)
        E/W   -> E_W    (BD)
        NW/SE -> NW_SE  (13)
        """
        mapping = {
            "N": "N_S",
            "S": "N_S",
            "NE": "NE_SW",
            "SW": "NE_SW",
            "E": "E_W",
            "W": "E_W",
            "NW": "NW_SE",
            "SE": "NW_SE",
        }
        direction = str(direction).upper()
        if direction not in mapping:
            raise ValueError(
                "direction must be N, NE, E, SE, S, SW, W, or NW"
            )
        return mapping[direction]

    def _world_to_stack_local(self, x, y, axis):
        """
        頭割り軸をローカル+Yとして、プレイヤー位置をローカル座標へ変換する。
        """
        heading_deg = {
            "N_S": 0.0,
            "NE_SW": 45.0,
            "E_W": 90.0,
            "NW_SE": -45.0,
        }[axis]

        heading = math.radians(heading_deg)

        # 軸方向（ローカル+Y）
        axis_x = math.sin(heading)
        axis_y = math.cos(heading)

        # 軸に向かって右側（ローカル+X）
        right_x = math.cos(heading)
        right_y = -math.sin(heading)

        local_x = x * right_x + y * right_y
        local_y = x * axis_x + y * axis_y
        return local_x, local_y

    def player_is_in_stack_area(self, direction=None):
        """
        指定方向を基準とした2か所の頭割り範囲内に
        プレイヤーがいるかを返す。

        direction=None:
            pattern["exdeath_direction"] を使用（早デバフ）
        direction="N":
            N_S軸固定（遅デバフ）
        """
        if direction is None:
            direction = self.pattern["exdeath_direction"]

        axis = self.get_stack_axis_from_exdeath_direction(direction)

        x = self.player.getX()
        y = self.player.getY()
        local_x, local_y = self._world_to_stack_local(x, y, axis)

        return (
            abs(local_x) <= self.STACK_HALF_WIDTH
            and self.STACK_INNER_DISTANCE
            <= abs(local_y)
            <= self.STACK_OUTER_DISTANCE
        )

    def setup_stack_area_debug(self):
        """
        頭割り判定範囲を半透明オレンジで可視化するデバッグ表示。

        実際の判定と同じ
          STACK_HALF_WIDTH
          STACK_INNER_DISTANCE
          STACK_OUTER_DISTANCE
        を使用する。
        """
        self.stack_area_debug_visible = False
        self.stack_area_debug_root = self.render.attachNewNode(
            "stack_area_debug_root"
        )
        self.stack_area_debug_root.setPos(0, 0, 0.075)

        width = self.STACK_HALF_WIDTH * 2.0
        length = self.STACK_OUTER_DISTANCE - self.STACK_INNER_DISTANCE
        center_offset = (
            self.STACK_INNER_DISTANCE + self.STACK_OUTER_DISTANCE
        ) / 2.0

        debug_color = (1.0, 0.45, 0.08, 0.28)
        self.stack_area_debug_nodes = []

        for sign in (1.0, -1.0):
            node = self.create_floor_rect(
                name=f"stack_area_debug_{'plus' if sign > 0 else 'minus'}",
                width=width,
                length=length,
                angle_deg=0.0,
                offset_x=0.0,
                offset_y=sign * center_offset,
                z=0.0,
                color=debug_color,
                sort_order=35,
            )
            node.reparentTo(self.stack_area_debug_root)
            self.stack_area_debug_nodes.append(node)

        self.stack_area_debug_root.hide()

    def update_stack_area_debug(self, direction=None):
        """
        頭割り判定範囲デバッグ表示の向きを更新する。

        direction=None -> exdeath_direction（早デバフ）
        direction="N"  -> N_S固定（遅デバフ）
        """
        if not hasattr(self, "stack_area_debug_root"):
            return

        if direction is None:
            direction = self.pattern["exdeath_direction"]

        axis = self.get_stack_axis_from_exdeath_direction(direction)

        # Panda3D の H 回転に合わせた軸角度。
        heading_deg = {
            "N_S": 0.0,
            "NE_SW": -45.0,
            "E_W": -90.0,
            "NW_SE": 45.0,
        }[axis]

        self.stack_area_debug_root.setH(heading_deg)

        if self.stack_area_debug_visible:
            self.stack_area_debug_root.show()
        else:
            self.stack_area_debug_root.hide()

    def show_stack_area_debug(self):
        self.stack_area_debug_visible = True
        self.update_stack_area_debug()

    def hide_stack_area_debug(self):
        self.stack_area_debug_visible = False
        if hasattr(self, "stack_area_debug_root"):
            self.stack_area_debug_root.hide()

    def check_early_personal_debuff(self):
        """
        70.1sec 早個人デバフ判定。

        最新仕様：
        water_light_result を参照する。
        SPREAD_EARLY -> 2か所の頭割り範囲「以外」が正解
        それ以外     -> 2か所の頭割り範囲「内」が正解

        ここでは water_light_time は参照しない。
        """
        in_stack_area = self.player_is_in_stack_area()
        result = self.derived["water_light_result"]

        if result == "SPREAD_EARLY":
            correct = not in_stack_area
        else:
            # STACK / SPREAD_LATE は、この時点では頭割り範囲内が正解。
            correct = in_stack_area

        if not correct:
            self.set_miss_text("頭割り/散会失敗")
            return False

        return True

    def _timeline_0701_early_activation_effect(self):
        # 成否・デバフ所持有無に関係なく、
        # 「早デバフの発動タイミング」を知らせる。
        self.update_stack_area_debug(
            self.pattern["exdeath_direction"]
        )
        self.show_activation_ring()

    def _timeline_0701_early_personal_check(self):
        self.check_early_personal_debuff()

    # ---------------------------
    # 加速度判定
    # ---------------------------
    def check_early_accel_debuff(self):
        """
        70.1sec 早加速度判定。

        accel_time="EARLY" の場合のみ判定。
        STOP -> その瞬間に実際の移動入力結果が0
        MOVE -> その瞬間に実際に移動している
        """
        if self.pattern["accel_time"] != "EARLY":
            return True

        moving = bool(getattr(self, "player_is_moving", False))
        result = self.derived["accel_result"]

        correct = (
            (not moving)
            if result == "STOP"
            else moving
        )

        if not correct:
            self.set_miss_text("加速度デバフ失敗")
            return False

        return True

    def _timeline_0701_early_accel_check(self):
        self.check_early_accel_debuff()

    # ---------------------------
    # Magic Charge 雷
    # ---------------------------
    def _timeline_0731_kefka_thunder_cast(self):
        self.start_enemy_cast(
            "kefka",
            "もりもりサンダガ",
            4.7,
        )

    def _timeline_0731_kefka_thunder_truth(self):
        # Magic Charge は雷だけ表示する。
        self.set_kefka_truth_effect(
            upper_visible=True,
            lower_visible=False,
            upper_truth=self.pattern["kefka_thunder"][3],
            lower_truth=True,
        )

    def _timeline_0731_kefka_thunder_floor(self):
        thunder_pattern = self.pattern["floor_thunder"][3]

        # 氷床は出さないが、既存の検証関数を使わず雷だけ検証する。
        valid_thunder = {
            "NE_SW_N",
            "NE_SW_S",
            "NW_SE_N",
            "NW_SE_S",
        }
        if thunder_pattern not in valid_thunder:
            raise ValueError(
                f"Invalid floor_thunder value: {thunder_pattern!r}"
            )

        self.kefka_floor_aoe_root.show()
        self.set_thunder_floor_pattern(
            thunder_pattern,
            visible=True,
        )
        self.blizzard_aoe_root.hide()

    def _timeline_0716_early_gaze_call(self):
        """
        71.6sec：
        視線（発動時）チェックONなら、
        exdeath_truth[0] に応じて音声を出す。
        """
        if not self.gui_flags.get("call_gaze", False):
            return

        filename = self.supplemental_sound_files[
            "shriek_truth"
        ][bool(self.pattern["exdeath_truth"][0])]

        self._play_supplemental_sound(filename)

    def _timeline_0741_magic_charge_thunder_call(self):
        """
        74.1sec：
        マジックチャージ床チェックONなら、
        kefka_thunder[3] に応じて音声を出す。
        """
        if not self.gui_flags.get("call_magic_charge_floor", False):
            return

        filename = self.supplemental_sound_files[
            "magic_charge_floor"
        ][bool(self.pattern["kefka_thunder"][3])]

        self._play_supplemental_sound(filename)

    def _timeline_0741_magic_charge_chat(self):
        truth = bool(self.pattern["kefka_thunder"][3])

        message = (
            "MC雷：本当"
            if truth
            else "MC雷：ウソ"
        )

        self.append_pt_chat_assist(
            "chat_charge_floor",
            message,
        )

    def check_kefka_thunder_position(self, index):
        """
        指定indexの雷床だけを判定する。
        true  -> 雷床表示がない場所が正解
        false -> 雷床表示がある場所が正解
        """
        on_thunder = self._player_is_on_thunder_visual(index)
        thunder_truth = self.pattern["kefka_thunder"][index]

        correct = (
            (not on_thunder)
            if thunder_truth
            else on_thunder
        )

        if not correct:
            self.set_miss_text("雷床を踏んだ")
            return False

        return True

    def _timeline_0785_kefka_thunder_end(self):
        self.check_kefka_thunder_position(index=3)
        self.thunder_aoe_root.hide()
        self.hide_kefka_truth_effect()

    # ---------------------------
    # 視線 / 叫声判定
    # ---------------------------
    def check_early_gaze(self):
        """
        79.1sec 早視線判定。

        最新仕様：
        exdeath_truth[0]=True  -> 中央を向いていない（見ない）が正解
        exdeath_truth[0]=False -> 中央を向いている（見る）が正解

        中央方向との内積を0で前後に二分する簡略判定。
        """
        dot = self.get_player_center_facing_dot()
        looking_center = dot > 0.0
        truth = bool(self.pattern["exdeath_truth"][0])

        correct = (
            not looking_center
            if truth
            else looking_center
        )

        if not correct:
            self.set_miss_text("視線処理失敗")
            return False

        return True

    def _timeline_0791_gaze_activation_effect(self):
        # 視線判定の瞬間を、短い白フラッシュで知らせる。
        self.show_gaze_flash()

    def _timeline_0791_early_gaze_check(self):
        self.check_early_gaze()

    def check_early_shriek_position(self):
        """
        79.1sec 叫声デバフ立ち位置判定。

        shriek_enabled=True かつ shriek_time="EARLY" の場合のみ判定。
        黄緑色のターゲットサークルより一回り小さい
        self.shriek_safe_radius の外側なら失敗。
        """
        if not self.pattern["shriek_enabled"]:
            return True
        if self.derived["shriek_time"] != "EARLY":
            return True

        x = self.player.getX()
        y = self.player.getY()
        radius = math.sqrt(x * x + y * y)

        if radius > self.shriek_safe_radius:
            self.set_miss_text("叫声デバフ立ち位置ミス")
            return False

        return True

    def _timeline_0791_early_shriek_position_check(self):
        self.check_early_shriek_position()

    def _timeline_0833_kefka_ultima_cast(self):
        self.start_enemy_cast(
            "kefka",
            "どきどきアルテマ",
            4.7,
        )

    # ---------------------------
    # 84.3～98.2秒：炎 / MC氷 / 遅デバフ
    # ---------------------------
    def _timeline_0843_fire_result_call(self):
        """84.3sec：ほのお発動時コール。"""
        if not self.gui_flags.get("call_fire_tsunami", False):
            return

        filename = self.supplemental_sound_files[
            "fire_result"
        ][self.derived["fire_result"]]
        self._play_supplemental_sound(filename)

    def _is_player_in_element_place_area(self):
        """
        ほのお/つなみ設置用の中央範囲内にいるか。

        仕様：
        黄緑色ターゲットサークル（field_radius * 0.3）の
        1/3サイズの円内。
        """
        x = self.player.getX()
        y = self.player.getY()
        radius = math.sqrt(x * x + y * y)

        place_radius = (self.field_radius * 0.3) / 3.0
        return radius <= place_radius

    def _timeline_0859_fire_place_check(self):
        if not self._is_player_in_element_place_area():
            self.set_miss_text("ほのお設置失敗")

    def _player_is_on_kefka_radial_aoe(self, aoe_type):
        """CHARIOT / DYNAMO の現在位置がAoE上かを返す。"""
        x = self.player.getX()
        y = self.player.getY()
        radius = math.sqrt(x * x + y * y)

        aoe_type = str(aoe_type).upper()
        if aoe_type == "CHARIOT":
            return radius <= self.kefka_chariot_radius
        if aoe_type == "DYNAMO":
            return radius >= self.kefka_chariot_radius

        raise ValueError("aoe_type must be CHARIOT or DYNAMO")

    def _timeline_0910_fire_radial_aoe(self):
        self.show_kefka_radial_aoe(
            self.derived["fire_result"]
        )

    def _timeline_0910_fire_radial_check(self):
        if self._player_is_on_kefka_radial_aoe(
            self.derived["fire_result"]
        ):
            self.set_miss_text("ほのお避け失敗")

    def _timeline_0913_kefka_blizzard_cast(self):
        self.start_enemy_cast(
            "kefka",
            "ひろげるブリザガ",
            4.7,
        )

    def _timeline_0913_kefka_blizzard_truth(self):
        self.set_kefka_truth_effect(
            upper_visible=False,
            lower_visible=True,
            upper_truth=True,
            lower_truth=self.pattern["kefka_blizzard"][3],
        )

    def _timeline_0913_kefka_blizzard_floor(self):
        blizzard_pattern = self.pattern["floor_blizzard"][3]

        if blizzard_pattern not in ("NE", "NW"):
            raise ValueError(
                f"Invalid floor_blizzard value: {blizzard_pattern!r}"
            )

        self.kefka_floor_aoe_root.show()
        self.thunder_aoe_root.hide()
        self.set_blizzard_floor_pattern(
            blizzard_pattern,
            visible=True,
        )

    def _timeline_0914_hide_radial_aoe(self):
        self.hide_kefka_radial_aoe()

    def _timeline_0923_magic_charge_blizzard_call(self):
        if not self.gui_flags.get("call_magic_charge_floor", False):
            return

        filename = self.supplemental_sound_files[
            "magic_charge_floor"
        ][bool(self.pattern["kefka_blizzard"][3])]

        self._play_supplemental_sound(filename)

    def _timeline_0923_magic_charge_blizzard_chat(self):
        truth = bool(self.pattern["kefka_blizzard"][3])
        message = "MC氷：本当" if truth else "MC氷：ウソ"

        self.append_pt_chat_assist(
            "chat_charge_floor",
            message,
        )

    def check_late_personal_debuff(self):
        """
        95.1sec 遅個人デバフ判定。

        SPREAD_LATE -> 頭割り範囲外
        それ以外    -> 頭割り範囲内

        頭割り範囲の基準方向は N 固定。
        """
        in_stack_area = self.player_is_in_stack_area(direction="N")
        result = self.derived["water_light_result"]

        if result == "SPREAD_LATE":
            correct = not in_stack_area
        else:
            correct = in_stack_area

        if not correct:
            self.set_miss_text("頭割り/散会失敗")
            return False

        return True

    def _timeline_0951_late_activation_effect(self):
        # 遅デバフの判定軸は北(N)固定。
        self.update_stack_area_debug("N")
        self.show_activation_ring()

    def _timeline_0951_late_personal_check(self):
        self.check_late_personal_debuff()

    def check_late_accel_debuff(self):
        """95.1sec 遅加速度判定。"""
        if self.pattern["accel_time"] != "LATE":
            return True

        moving = bool(getattr(self, "player_is_moving", False))
        result = self.derived["accel_result"]

        correct = (
            (not moving)
            if result == "STOP"
            else moving
        )

        if not correct:
            self.set_miss_text("加速度デバフ失敗")
            return False

        return True

    def _timeline_0951_late_accel_check(self):
        self.check_late_accel_debuff()

    def check_kefka_blizzard_position(self, index):
        """指定indexの氷床だけを判定する。"""
        on_blizzard = self._player_is_on_blizzard_visual(index)
        blizzard_truth = self.pattern["kefka_blizzard"][index]

        correct = (
            (not on_blizzard)
            if blizzard_truth
            else on_blizzard
        )

        if not correct:
            self.set_miss_text("氷床を踏んだ")
            return False

        return True

    def _timeline_0967_kefka_blizzard_end(self):
        self.check_kefka_blizzard_position(index=3)
        self.blizzard_aoe_root.hide()
        self.hide_kefka_truth_effect()

    def _timeline_0982_late_gaze_call(self):
        """98.2sec：遅視線の発動時コール。"""
        if not self.gui_flags.get("call_gaze", False):
            return

        filename = self.supplemental_sound_files[
            "shriek_truth"
        ][bool(self.pattern["exdeath_truth"][1])]

        self._play_supplemental_sound(filename)

    # ---------------------------
    # 102.5～122.3秒：Magic Out
    # ---------------------------
    def _timeline_1025_magic_out_cast(self):
        self.start_enemy_cast(
            "kefka",
            "マジックアウト",
            6.7,
        )

    def _timeline_1025_magic_out_truth(self):
        self.set_kefka_truth_effect(
            upper_visible=True,
            lower_visible=True,
            upper_truth=self.pattern["kefka_thunder"][4],
            lower_truth=self.pattern["kefka_blizzard"][4],
        )

    def check_late_gaze(self):
        """103.1sec 遅視線判定。"""
        dot = self.get_player_center_facing_dot()
        looking_center = dot > 0.0
        truth = bool(self.pattern["exdeath_truth"][1])

        correct = (
            not looking_center
            if truth
            else looking_center
        )

        if not correct:
            self.set_miss_text("視線処理失敗")
            return False

        return True

    def _timeline_1031_gaze_activation_effect(self):
        self.show_gaze_flash()

    def _timeline_1031_late_gaze_check(self):
        self.check_late_gaze()

    def check_late_shriek_position(self):
        """
        103.1sec 遅叫声判定。
        shriek_enabled=True かつ shriek_time="LATE" の場合のみ。
        """
        if not self.pattern["shriek_enabled"]:
            return True
        if self.derived["shriek_time"] != "LATE":
            return True

        x = self.player.getX()
        y = self.player.getY()
        radius = math.sqrt(x * x + y * y)

        if radius > self.shriek_safe_radius:
            self.set_miss_text("叫声デバフ立ち位置ミス")
            return False

        return True

    def _timeline_1031_late_shriek_position_check(self):
        self.check_late_shriek_position()

    def _timeline_1061_tsunami_then_magic_out_call(self):
        self.play_tsunami_then_magic_out_call(gap=0.3)

    def _timeline_1077_magic_out_truth_end(self):
        self.hide_kefka_truth_effect()

    def _timeline_1087_tsunami_place_check(self):
        if not self._is_player_in_element_place_area():
            self.set_miss_text("つなみ設置失敗")

    def _timeline_1095_magic_out_floor(self):
        thunder_pattern = self.pattern["floor_thunder"][4]
        blizzard_pattern = self.pattern["floor_blizzard"][4]

        self._validate_kefka_floor_pattern(
            thunder_pattern,
            blizzard_pattern,
        )

        self.set_kefka_floor_aoe(
            thunder_pattern=thunder_pattern,
            blizzard_pattern=blizzard_pattern,
            thunder_visible=True,
            blizzard_visible=True,
        )

    def _timeline_1138_tsunami_radial_aoe(self):
        self.show_kefka_radial_aoe(
            self.derived["tsunami_result"]
        )

    def _timeline_1138_tsunami_radial_check(self):
        if self._player_is_on_kefka_radial_aoe(
            self.derived["tsunami_result"]
        ):
            self.set_miss_text("つなみ避け失敗")

    def check_magic_out_floor_position(self):
        """
        Magic Out床の最終判定。

        雷: magic_out_thunder_truth
        氷: magic_out_blizzard_truth
        を使用する。
        """
        on_thunder = self._player_is_on_thunder_visual(index=4)
        on_blizzard = self._player_is_on_blizzard_visual(index=4)

        thunder_truth = self.derived["magic_out_thunder_truth"]
        blizzard_truth = self.derived["magic_out_blizzard_truth"]

        thunder_correct = (
            (not on_thunder)
            if thunder_truth
            else on_thunder
        )
        blizzard_correct = (
            (not on_blizzard)
            if blizzard_truth
            else on_blizzard
        )

        if not (thunder_correct and blizzard_correct):
            self.set_miss_text("雷/氷床を踏んだ")
            return False

        return True

    def _timeline_1142_magic_out_floor_check(self):
        # 判定だけ行い、床表示は115.2秒まで残す。
        self.check_magic_out_floor_position()

    def _timeline_1152_hide_radial_aoe(self):
        self.hide_kefka_radial_aoe()

    def _timeline_1152_magic_out_floor_end(self):
        self.hide_kefka_floor_aoe()

    def _timeline_1223_kefka_ultima_cast(self):
        self.start_enemy_cast(
            "kefka",
            "どきどきアルテマ",
            4.7,
        )

    def setup_kefka_radial_aoe(self):
        """
        ケフカ中心の Chariot / Dynamo の見た目を作成する。

        Chariot:
            黄緑色の内周円（タゲサ）より少し大きい円形AoE。

        Dynamo:
            Chariot範囲を反転したドーナツ状AoE。
            内径はChariot半径、外径はフィールド半径。

        ここでは見た目だけを管理し、Hit判定は別途実装する。
        """
        self.kefka_radial_aoe_color = (1.0, 0.46, 0.08, 0.38)

        # 黄緑色の内周円は field_radius * 0.3。
        # Chariotはそれより少しだけ大きくする。
        self.kefka_chariot_radius = self.field_radius * 0.33
        self.kefka_dynamo_outer_radius = self.field_radius

        # フィールド床より上、マーカー等を極力邪魔しない高さ。
        self.kefka_radial_aoe_z = 0.014

        self.kefka_radial_aoe_root = self.render.attachNewNode(
            "kefka_radial_aoe_root"
        )

        # Chariot: 塗りつぶし円
        self.kefka_chariot_node = self.create_disc_node(
            name="kefka_chariot_aoe",
            radius=self.kefka_chariot_radius,
            color=self.kefka_radial_aoe_color,
            z=self.kefka_radial_aoe_z,
            segments=192,
        )
        self.kefka_chariot_node.reparentTo(self.kefka_radial_aoe_root)
        self.kefka_chariot_node.setDepthWrite(False)
        self.kefka_chariot_node.setBin("transparent", 25)

        # Dynamo: Chariotの外側～フィールド外周までのドーナツ
        self.kefka_dynamo_node = self.create_ring_node(
            name="kefka_dynamo_aoe",
            inner_radius=self.kefka_chariot_radius,
            outer_radius=self.kefka_dynamo_outer_radius,
            segments=256,
            color=self.kefka_radial_aoe_color,
        )
        self.kefka_dynamo_node.reparentTo(self.kefka_radial_aoe_root)
        self.kefka_dynamo_node.setPos(0, 0, self.kefka_radial_aoe_z)
        self.kefka_dynamo_node.setTransparency(TransparencyAttrib.MAlpha)
        self.kefka_dynamo_node.setDepthWrite(False)
        self.kefka_dynamo_node.setBin("transparent", 25)

        self.kefka_chariot_node.hide()
        self.kefka_dynamo_node.hide()

    def show_kefka_radial_aoe(self, aoe_type):
        """
        Chariot / Dynamo のどちらか一方を選んで表示する。

        aoe_type:
            "CHARIOT" -> ケフカ中心の円形AoE
            "DYNAMO"  -> Chariot範囲の外側となるドーナツAoE

        常にどちらか片方だけが表示される。
        """
        aoe_type = str(aoe_type).upper()

        if aoe_type == "CHARIOT":
            self.kefka_dynamo_node.hide()
            self.kefka_chariot_node.show()

        elif aoe_type == "DYNAMO":
            self.kefka_chariot_node.hide()
            self.kefka_dynamo_node.show()

        else:
            raise ValueError(
                "aoe_type must be 'CHARIOT' or 'DYNAMO'"
            )

        self.kefka_radial_aoe_root.show()

    def hide_kefka_radial_aoe(self):
        """
        現在表示中の Chariot / Dynamo を消す。
        次に show_kefka_radial_aoe() が呼ばれるまで両方非表示。
        """
        self.kefka_chariot_node.hide()
        self.kefka_dynamo_node.hide()
        self.kefka_radial_aoe_root.hide()

    def setup_kefka_floor_aoe(self):
        """
        ケフカの床AoE「見た目」だけを管理する。

        雷床:
            NE_SW_N / NE_SW_S / NW_SE_N / NW_SE_S
            斜め方向に走る平行な2本の帯。

        氷床:
            NE / NW
            対角の2象限を塗る。

        真偽によって実際にどこがHitするかは、ここでは扱わない。
        """
        # 実機参考画像に寄せた、紫～ピンクの中間色。
        # 半透明にして、雷と氷が重なった場所は濃く見えるようにする。
        self.kefka_floor_aoe_color = (0.72, 0.16, 0.48, 0.34)

        self.kefka_floor_aoe_root = self.render.attachNewNode(
            "kefka_floor_aoe_root"
        )

        self.thunder_aoe_root = self.kefka_floor_aoe_root.attachNewNode(
            "thunder_aoe_root"
        )
        self.blizzard_aoe_root = self.kefka_floor_aoe_root.attachNewNode(
            "blizzard_aoe_root"
        )

        self.current_thunder_pattern = None
        self.current_blizzard_pattern = None

        # 見た目調整用パラメータ
        # 雷床は、帯に垂直な方向から見たときに
        # フィールド直径を「AoE / 非AoE / AoE / 非AoE」の
        # 4等分にする。
        #
        # field_radius = 12 の場合：
        #   フィールド直径 = 24
        #   1区画 = 24 / 4 = 6
        # よって帯の幅は半径のちょうど1/2 (= 6)。
        self.thunder_band_width = self.field_radius / 2.0
        self.thunder_band_length = self.field_radius * 3.4

        # 4等分された区画の中心位置。
        #
        # 外側区画中心：±9  = ±(3/4 * radius)
        # 内側区画中心：±3  = ±(1/4 * radius)
        #
        # Nパターン：
        #   +9 の帯 / +3 の空白 / -3 の帯 / -9 の空白
        #
        # Sパターン：
        #   +9 の空白 / +3 の帯 / -3 の空白 / -9 の帯
        self.thunder_outer_offset = self.field_radius * 0.75
        self.thunder_inner_offset = self.field_radius * 0.25

        # AoEは地面より上、フィールドマーカー等より下に置く
        self.thunder_aoe_z = 0.06
        self.blizzard_aoe_z = 0.05

    def _configure_floor_aoe_node(self, node, sort_order):
        """
        半透明床AoE向けの描画設定。
        depth writeを切ることで、雷と氷が重なった部分も
        アルファブレンドされて濃く見えるようにする。
        """
        node.setTransparency(TransparencyAttrib.MAlpha)
        node.setDepthWrite(False)
        node.setBin("transparent", sort_order)

    def create_floor_rect(
        self,
        name,
        width,
        length,
        angle_deg,
        offset_x=0.0,
        offset_y=0.0,
        z=0.014,
        color=None,
        sort_order=10,
    ):
        """XY平面上に、回転可能な長方形AoEを作る。"""
        if color is None:
            color = self.kefka_floor_aoe_color

        half_w = width / 2.0
        half_l = length / 2.0

        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(name, fmt, Geom.UHStatic)
        vw = GeomVertexWriter(vdata, "vertex")
        cw = GeomVertexWriter(vdata, "color")

        # ローカルではY方向に長い長方形
        points = [
            (-half_w, -half_l, 0.0),
            ( half_w, -half_l, 0.0),
            ( half_w,  half_l, 0.0),
            (-half_w,  half_l, 0.0),
        ]

        for px, py, pz in points:
            vw.addData3f(px, py, pz)
            cw.addData4f(*color)

        tris = GeomTriangles(Geom.UHStatic)
        tris.addVertices(0, 1, 2)
        tris.addVertices(0, 2, 3)

        geom = Geom(vdata)
        geom.addPrimitive(tris)

        gnode = GeomNode(name)
        gnode.addGeom(geom)

        node = self.render.attachNewNode(gnode)
        node.setPos(offset_x, offset_y, z)
        node.setH(angle_deg)
        self._configure_floor_aoe_node(node, sort_order)
        return node

    def create_floor_rects_combined(
        self,
        name,
        width,
        length,
        angle_deg,
        offsets,
        z=0.06,
        color=None,
        sort_order=10,
    ):
        """
        複数の平行な長方形AoEを1つのGeomにまとめて作る。

        offsets:
            [(offset_x, offset_y), ...] のリスト

        雷床を別Nodeで2本描画すると、半透明描画のソートにより
        カメラ角度によって片方だけ床に埋まって見えることがあるため、
        2本を1つのGeomとして描画する。
        """
        if color is None:
            color = self.kefka_floor_aoe_color

        half_w = width / 2.0
        half_l = length / 2.0

        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(name, fmt, Geom.UHStatic)
        vw = GeomVertexWriter(vdata, "vertex")
        cw = GeomVertexWriter(vdata, "color")
        tris = GeomTriangles(Geom.UHStatic)

        # ローカルY方向に長い長方形を作り、頂点側で回転・平行移動する。
        theta = math.radians(angle_deg)
        c = math.cos(theta)
        s = math.sin(theta)

        local_points = [
            (-half_w, -half_l),
            ( half_w, -half_l),
            ( half_w,  half_l),
            (-half_w,  half_l),
        ]

        vertex_index = 0

        for offset_x, offset_y in offsets:
            base = vertex_index

            for lx, ly in local_points:
                # create_floor_rect() の setH(angle_deg) と同じ見え方になるよう
                # XY平面上で回転させる。
                x = lx * c - ly * s + offset_x
                y = lx * s + ly * c + offset_y

                vw.addData3f(x, y, 0.0)
                cw.addData4f(*color)
                vertex_index += 1

            tris.addVertices(base + 0, base + 1, base + 2)
            tris.addVertices(base + 0, base + 2, base + 3)

        geom = Geom(vdata)
        geom.addPrimitive(tris)

        gnode = GeomNode(name)
        gnode.addGeom(geom)

        node = self.render.attachNewNode(gnode)
        node.setPos(0, 0, z)
        self._configure_floor_aoe_node(node, sort_order)
        return node

    def create_floor_sectors_combined(
        self,
        name,
        sectors,
        radius=None,
        z=0.013,
        color=None,
        segments_per_sector=48,
        sort_order=11,
    ):
        """
        複数の扇形を1つのGeomにまとめて作成する。

        半透明の扇を別々のNodeとして描画すると、
        視点によって透明オブジェクトのソート順が不安定になる場合があるため、
        氷床の対角2象限は1つのGeomとして描画する。
        """
        if radius is None:
            radius = self.field_radius
        if color is None:
            color = self.kefka_floor_aoe_color

        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(name, fmt, Geom.UHStatic)
        vw = GeomVertexWriter(vdata, "vertex")
        cw = GeomVertexWriter(vdata, "color")
        tris = GeomTriangles(Geom.UHStatic)

        vertex_index = 0

        for sector_index, (start_deg, end_deg) in enumerate(sectors):
            # 各扇の中心頂点
            center_index = vertex_index
            vw.addData3f(0.0, 0.0, 0.0)
            cw.addData4f(*color)
            vertex_index += 1

            arc_start_index = vertex_index

            # 円弧上の頂点
            for i in range(segments_per_sector + 1):
                t = (
                    start_deg
                    + (end_deg - start_deg) * i / segments_per_sector
                )
                rad = math.radians(t)
                x = math.cos(rad) * radius
                y = math.sin(rad) * radius

                vw.addData3f(x, y, 0.0)
                cw.addData4f(*color)
                vertex_index += 1

            # 扇形の三角形
            for i in range(segments_per_sector):
                tris.addVertices(
                    center_index,
                    arc_start_index + i,
                    arc_start_index + i + 1,
                )

        geom = Geom(vdata)
        geom.addPrimitive(tris)

        gnode = GeomNode(name)
        gnode.addGeom(geom)

        node = self.render.attachNewNode(gnode)
        node.setPos(0, 0, z)
        self._configure_floor_aoe_node(node, sort_order)
        return node

    def _clear_node_children(self, node):
        """指定ノード配下の描画物をすべて消す。"""
        for child in node.getChildren():
            child.removeNode()

    def set_thunder_floor_pattern(self, pattern, visible=True):
        """
        雷床の見た目を更新する。

        pattern:
            NE_SW_N / NE_SW_S / NW_SE_N / NW_SE_S
        """
        valid = {
            "NE_SW_N",
            "NE_SW_S",
            "NW_SE_N",
            "NW_SE_S",
        }
        if pattern not in valid:
            raise ValueError(
                "thunder pattern must be "
                "NE_SW_N, NE_SW_S, NW_SE_N, or NW_SE_S"
            )

        self._clear_node_children(self.thunder_aoe_root)
        self.current_thunder_pattern = pattern

        # 斜線方向と、2本目をずらす方向を明示的に決める。
        # NE_SW は NW/SE 側へ、NW_SE は NE/SW 側へずらす。
        # 以前の法線計算では2本目が帯の長手方向へずれてしまい、
        # ほぼ同じ帯に重なって見える場合があった。
        inv_sqrt2 = 1.0 / math.sqrt(2.0)

        if pattern.startswith("NE_SW"):
            angle = -45.0
            north_offset_dir = Vec2(-inv_sqrt2, inv_sqrt2)  # NW
        else:
            angle = 45.0
            north_offset_dir = Vec2(inv_sqrt2, inv_sqrt2)   # NE

        side = pattern.rsplit("_", 1)[1]  # N / S
        side_sign = 1.0 if side == "N" else -1.0

        # 雷床はどちらの帯もフィールド中心を通らない。
        #
        # N:
        #   北側へ大きくずれた帯 + 南側へ少しずれた帯
        #
        # S:
        #   北側へ少しずれた帯 + 南側へ大きくずれた帯
        #
        # これで N/S によって2本全体の位相が切り替わる。
        if side == "N":
            signed_offsets = (
                self.thunder_outer_offset,
                -self.thunder_inner_offset,
            )
        else:
            signed_offsets = (
                self.thunder_inner_offset,
                -self.thunder_outer_offset,
            )

        rect_offsets = [
            (
                north_offset_dir.x * offset,
                north_offset_dir.y * offset,
            )
            for offset in signed_offsets
        ]

        # 2本を同じGeomにまとめる。
        # 半透明Node同士の描画順問題も同時に回避する。
        bands = self.create_floor_rects_combined(
            name=f"thunder_{pattern}",
            width=self.thunder_band_width,
            length=self.thunder_band_length,
            angle_deg=angle,
            offsets=rect_offsets,
            z=self.thunder_aoe_z,
            sort_order=10,
        )
        bands.reparentTo(self.thunder_aoe_root)

        if visible:
            self.thunder_aoe_root.show()
        else:
            self.thunder_aoe_root.hide()

    def set_blizzard_floor_pattern(self, pattern, visible=True):
        """
        氷床の見た目を更新する。

        NE:
            北東 + 南西
        NW:
            北西 + 南東
        """
        if pattern not in ("NE", "NW"):
            raise ValueError("blizzard pattern must be NE or NW")

        self._clear_node_children(self.blizzard_aoe_root)
        self.current_blizzard_pattern = pattern

        if pattern == "NE":
            sectors = [
                (0.0, 90.0),       # NE
                (180.0, 270.0),    # SW
            ]
        else:
            sectors = [
                (90.0, 180.0),     # NW
                (270.0, 360.0),    # SE
            ]

        # 対角2象限を1つのGeomとして描画する。
        # 半透明オブジェクトを別Nodeに分けた際の
        # カメラ依存の描画順問題を避けるため。
        sector_node = self.create_floor_sectors_combined(
            name=f"blizzard_{pattern}",
            sectors=sectors,
            radius=self.field_radius,
            z=self.blizzard_aoe_z,
            sort_order=11,
        )
        sector_node.reparentTo(self.blizzard_aoe_root)

        if visible:
            self.blizzard_aoe_root.show()
        else:
            self.blizzard_aoe_root.hide()

    def set_kefka_floor_aoe(
        self,
        thunder_pattern=None,
        blizzard_pattern=None,
        thunder_visible=True,
        blizzard_visible=True,
    ):
        """雷床・氷床の見た目をまとめて設定する。"""
        self.kefka_floor_aoe_root.show()
        if thunder_pattern is not None:
            # self.pattern["floor_thunder"] はタイムライン全体で使う
            # 4要素のランダム配列なので、ここでは上書きしない。
            self.set_thunder_floor_pattern(
                thunder_pattern,
                visible=thunder_visible,
            )
        else:
            if thunder_visible:
                self.thunder_aoe_root.show()
            else:
                self.thunder_aoe_root.hide()

        if blizzard_pattern is not None:
            # self.pattern["floor_blizzard"] も4要素のランダム配列。
            # 表示関数側ではpattern辞書を書き換えない。
            self.set_blizzard_floor_pattern(
                blizzard_pattern,
                visible=blizzard_visible,
            )
        else:
            if blizzard_visible:
                self.blizzard_aoe_root.show()
            else:
                self.blizzard_aoe_root.hide()

    def hide_kefka_floor_aoe(self):
        """雷床・氷床をまとめて非表示にする。"""
        self.thunder_aoe_root.hide()
        self.blizzard_aoe_root.hide()

    def show_kefka_floor_aoe(self):
        """雷床・氷床をまとめて表示する。"""
        self.kefka_floor_aoe_root.show()
        self.thunder_aoe_root.show()
        self.blizzard_aoe_root.show()

    def apply_timeline_initial_state(self):
        """
        タイムライン開始前のデフォルト状態。

        表示:
            ・ケフカ本体のみ

        非表示:
            ・カオス本体
            ・エクスデス本体
            ・各種真偽エフェクト
            ・ケフカ雷/氷床
            ・Chariot / Dynamo
            ・エクスデスのビーム/ビーム床

        親root自体は可能な限りhideせず、
        後のshow()が確実に効くように専用hide関数で子を消す。
        """
        # 敵本体
        self.enemies["kefka"].show()
        self.enemies["chaos"].hide()
        self.enemies["exdeath"].hide()

        # ケフカ関連
        if hasattr(self, "kefka_truth_effect"):
            self.hide_kefka_truth_effect()

        if hasattr(self, "kefka_floor_aoe_root"):
            self.kefka_floor_aoe_root.show()
            self.hide_kefka_floor_aoe()

        if hasattr(self, "kefka_radial_aoe_root"):
            self.hide_kefka_radial_aoe()

        # カオス関連
        if hasattr(self, "chaos_truth_effect"):
            self.hide_chaos_truth_effect()

        # エクスデス関連
        if hasattr(self, "exdeath_truth_effect"):
            self.hide_exdeath_truth_effect()

        if hasattr(self, "exdeath_beam_root"):
            self.hide_exdeath_beam_effect()

        if hasattr(self, "exdeath_beam_floor_root"):
            self.hide_exdeath_beam_floor()

    def setup_debuff_display(self):
        """
        デバフ表示欄を作成する。

        Excel「デバフ仕様」準拠：
        ・優先順の昇順で左詰め
        ・後から優先度の高いデバフが付いた場合も並び替える
        ・未付与デバフは枠ごと完全非表示
        ・画像は ./img/debuff/ から読み込む
        ・画像の元サイズは24x32想定
        ・カウントダウン表示はアイコン下部
        ・60秒超は将来的に「1m」表示へ対応予定
        """
        self.debuff_icon_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "img",
            "debuff",
        )

        # 「デバフ仕様」シートの優先順・ファイル定義
        self.debuff_defs = {
            "fire": {
                "priority": 1,
                "file1": "fire.png",
                "file2": None,
            },
            "tsunami": {
                "priority": 2,
                "file1": "tsunami.png",
                "file2": None,
            },
            "accel": {
                "priority": 3,
                "file1": "accel.png",
                "file2": None,
            },
            "water_light": {
                "priority": 4,
                "file1": "water.png",
                "file2": "light.png",
            },
            "shriek": {
                "priority": 5,
                "file1": "shriek.png",
                "file2": None,
            },
            "wound_type": {
                "priority": 6,
                "file1": "death.png",
                "file2": "allagan.png",
            },
            "wound_color": {
                "priority": 7,
                "file1": "blue.png",
                "file2": "purple.png",
                # wound_colorに応じて付与時に一度だけ決定。
                # ギミック途中で青⇔紫の切替は行わない簡略仕様。
            },
        }

        # 現在付与中のデバフ
        # key -> {"filename": str, "remaining": float|None}
        self.active_debuffs = {}

        # 1920x810 / aspect2d 基準
        pixels_per_unit = 810 / 2.0

        # 元画像24x32の比率を保ちつつ、ゲーム画面上で見やすい大きさへ拡大。
        self.debuff_icon_width_px = 36
        self.debuff_icon_height_px = 48
        self.debuff_gap_px = 4

        self.debuff_icon_w = self.debuff_icon_width_px / pixels_per_unit
        self.debuff_icon_h = self.debuff_icon_height_px / pixels_per_unit
        self.debuff_gap = self.debuff_gap_px / pixels_per_unit

        # デバフ列の位置
        self.debuff_start_x = -2.00
        self.debuff_center_z = -0.04

        # カウントダウン用の太字フォント。
        # Windows標準のMeiryo Boldがあれば使い、無ければ通常フォントへフォールバック。
        self.debuff_countdown_font = self.gui_font
        bold_font_path = r"C:\Windows\Fonts\meiryob.ttc"
        if os.path.exists(bold_font_path):
            try:
                self.debuff_countdown_font = self.loader.loadFont(
                    Filename.fromOsSpecific(bold_font_path).getFullpath()
                )
            except Exception:
                self.debuff_countdown_font = self.gui_font

        self.debuff_slots = []

        # 最大7種類ぶんの表示枠だけ先に作る。
        for index in range(7):
            slot_root = self.aspect2d.attachNewNode(f"debuff_slot_{index}")

            # アイコン画像用のカード
            cm = CardMaker(f"debuff_icon_card_{index}")
            cm.setFrame(
                -self.debuff_icon_w / 2,
                self.debuff_icon_w / 2,
                -self.debuff_icon_h / 2,
                self.debuff_icon_h / 2,
            )
            icon = slot_root.attachNewNode(cm.generate())
            icon.setTransparency(TransparencyAttrib.MAlpha)
            icon.setColor(0.18, 0.18, 0.18, 1.0)

            # 画像が見つからない場合にファイル名を表示する仮テキスト
            fallback = OnscreenText(
                parent=slot_root,
                text="",
                pos=(0, 0.006),
                scale=0.022,
                align=TextNode.ACenter,
                fg=(1.0, 1.0, 1.0, 1.0),
                font=self.gui_font,
                mayChange=True,
            )

            # カウントダウン
            countdown = OnscreenText(
                parent=slot_root,
                text="",
                # アイコン下端寄りへ移動
                pos=(0, -self.debuff_icon_h * 0.62),
                # 前版より大きく
                scale=0.041,
                align=TextNode.ACenter,
                fg=(1.0, 1.0, 1.0, 1.0),
                # Meiryo Boldを優先
                font=self.debuff_countdown_font,
                # 輪郭感を出して視認性を上げる
                shadow=(0.0, 0.0, 0.0, 1.0),
                shadowOffset=(0.0025, 0.0025),
                mayChange=True,
            )

            slot_root.hide()

            self.debuff_slots.append({
                "root": slot_root,
                "icon": icon,
                "fallback": fallback,
                "countdown": countdown,
                "debuff_key": None,
            })

    def _format_debuff_countdown(self, remaining):
        """デバフ残り時間の表示文字列を返す。"""
        if remaining is None:
            return ""

        # 仕様：60秒を超えている間は「1m」。
        if remaining > 60:
            return "1m"

        # 現段階では整数秒表示。
        return str(max(0, int(math.ceil(remaining))))

    def add_debuff(self, debuff_key, filename=None, countdown=None):
        """
        デバフを付与または更新する。

        filename省略時は file1 を使用。
        countdown=None の場合はカウントダウンなし。
        """
        if debuff_key not in self.debuff_defs:
            raise ValueError(f"Unknown debuff key: {debuff_key}")

        definition = self.debuff_defs[debuff_key]

        if filename is None:
            filename = definition["file1"]

        self.active_debuffs[debuff_key] = {
            "filename": filename,
            "remaining": None if countdown is None else float(countdown),
        }

        self.refresh_debuff_display()

    def remove_debuff(self, debuff_key):
        """
        指定デバフを消去する。

        wound_type（death.png / allagan.png）が消える場合は、
        カウントダウンを持たない wound_color（blue.png / purple.png）も
        同時に消去する。
        """
        self.active_debuffs.pop(debuff_key, None)

        if debuff_key == "wound_type":
            self.active_debuffs.pop("wound_color", None)

        self.refresh_debuff_display()

    def clear_debuffs(self):
        """全デバフを消去する。"""
        self.active_debuffs.clear()
        self.refresh_debuff_display()

    def refresh_debuff_display(self):
        """
        現在付与中のデバフを優先順の昇順で左詰め表示する。
        """
        sorted_items = sorted(
            self.active_debuffs.items(),
            key=lambda item: self.debuff_defs[item[0]]["priority"],
        )

        for index, slot in enumerate(self.debuff_slots):
            if index >= len(sorted_items):
                slot["root"].hide()
                slot["debuff_key"] = None
                continue

            debuff_key, state = sorted_items[index]
            filename = state["filename"]

            x = self.debuff_start_x + index * (
                self.debuff_icon_w + self.debuff_gap
            )
            slot["root"].setPos(x, 0, self.debuff_center_z)

            texture_path = os.path.join(
                self.debuff_icon_dir,
                filename,
            )

            # Panda3D側で存在確認できる場合は画像を表示。
            # 開発環境に画像がまだ無い場合は、枠＋ファイル名で代替表示する。
            if os.path.exists(texture_path):
                texture = self.loader.loadTexture(
                    Filename.fromOsSpecific(texture_path)
                )
                if texture is not None:
                    slot["icon"].setTexture(texture, 1)
                    slot["icon"].setColor(1, 1, 1, 1)
                    slot["fallback"].setText("")
                else:
                    slot["icon"].clearTexture()
                    slot["icon"].setColor(0.18, 0.18, 0.18, 1.0)
                    slot["fallback"].setText(filename)
            else:
                slot["icon"].clearTexture()
                slot["icon"].setColor(0.18, 0.18, 0.18, 1.0)
                slot["fallback"].setText(filename)

            slot["countdown"].setText(
                self._format_debuff_countdown(state["remaining"])
            )
            slot["debuff_key"] = debuff_key
            slot["root"].show()

    def clear_all_debuffs(self):
        """現在表示中のデバフを全て解除する。"""
        self.active_debuffs.clear()
        self.refresh_debuff_display()

    def show_debug_debuffs(self):
        """
        仮表示：
        全種類でファイル1を選び、
        blue.png以外は一律30秒。
        """
        self.clear_debuffs()

        debug_values = [
            ("fire", "fire.png", 30),
            ("tsunami", "tsunami.png", 30),
            ("accel", "accel.png", 30),
            ("water_light", "water.png", 30),
            ("shriek", "shriek.png", 30),
            ("wound_type", "death.png", 30),
            ("wound_color", "blue.png", None),
        ]

        for debuff_key, filename, countdown in debug_values:
            self.active_debuffs[debuff_key] = {
                "filename": filename,
                "remaining": countdown,
            }

        self.refresh_debuff_display()

    def update_debuffs(self, dt):
        """
        カウントダウンを進め、0になったデバフを消去する。
        今後、実際の付与タイミング・残り時間仕様を接続する。
        """
        expired = []

        for debuff_key, state in self.active_debuffs.items():
            remaining = state["remaining"]

            if remaining is None:
                continue

            remaining -= dt
            state["remaining"] = remaining

            if remaining <= 0:
                expired.append(debuff_key)

        for debuff_key in expired:
            self.active_debuffs.pop(debuff_key, None)

            # death.png / allagan.png が時間切れで消えたら、
            # blue.png / purple.png も同時に消す。
            if debuff_key == "wound_type":
                self.active_debuffs.pop("wound_color", None)

        # 表示文字更新・0秒消去後の左詰めを反映
        self.refresh_debuff_display()

    def setup_action_button_grid(self):
        """
        右下の5x5ボタンをチャットマクロとして設定する。

        Excel「マクロ一覧」準拠：
        ・画像は ./img/macro/ から読み込む
        ・定義済みセルは画像を表示
        ・クリックでエコー欄 / PTチャット欄へ文章を追加
        ・「何もしない」はクリックしても何も出力しない
        ・未定義セルは空白枠のまま
        """
        self.action_buttons = []

        self.macro_icon_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "img",
            "macro",
        )

        # Excel「マクロ一覧」の行・列は1始まり。
        # key = (行, 列)
        self.macro_defs = {
            (1, 1): {
                "file": "attention.png",
                "target": "echo",
                "message": "◎動かない",
            },
            (1, 2): {
                "file": "dance.png",
                "target": "echo",
                "message": "？動く",
            },
            (1, 4): {
                "file": "katon.png",
                "target": "pt",
                "message": "炎：外（たけのこ）",
            },
            (1, 5): {
                "file": "katon.png",
                "target": "pt",
                "message": "炎：中（ドーナツ）",
            },

            (2, 1): {
                "file": "raiton.png",
                "target": "none",
                "message": "",
            },
            (2, 2): {
                "file": "meisui.png",
                "target": "none",
                "message": "",
            },
            (2, 4): {
                "file": "suiton.png",
                "target": "pt",
                "message": "つなみ：中（ドーナツ）",
            },
            (2, 5): {
                "file": "suiton.png",
                "target": "pt",
                "message": "つなみ：外（たけのこ）",
            },

            (3, 1): {
                "file": "attack1.png",
                "target": "echo",
                "message": "早散会（ビーム受けたまま）",
                "marker": "1",
            },
            (3, 2): {
                "file": "attack1.png",
                "target": "echo",
                "message": "早散会（ビーム受けたまま）",
                "marker": "1",
            },
            (3, 4): {
                "file": "facepalm.png",
                "target": "pt",
                "message": "視線１：見ない（本当）",
            },
            (3, 5): {
                "file": "surprise.png",
                "target": "pt",
                "message": "視線１：見る（ウソ）",
            },

            (4, 1): {
                "file": "attack2.png",
                "target": "echo",
                "message": "遅散会（東西）",
                "marker": "2",
            },
            (4, 2): {
                "file": "attack2.png",
                "target": "echo",
                "message": "遅散会（東西）",
                "marker": "2",
            },

            (4, 4): {
                "file": "facepalm.png",
                "target": "pt",
                "message": "視線２：見ない（本当）",
            },
            (4, 5): {
                "file": "surprise.png",
                "target": "pt",
                "message": "視線２：見る（ウソ）",
            },

            (5, 1): {
                "file": "backflip.png",
                "target": "none",
                "message": "",
            },
            (5, 2): {
                "file": "icon_3.png",
                "target": "echo",
                "message": (
                    "--------------------------------------------\n"
                    "〇踏まない 　|　●たて踏む(雷)\n"
                    "〇　　　　　|　〇\n"
                    "--------------------------------------------\n"
                    "〇扇踏む(氷) |　●両方踏む\n"
                    "●　　　　　|　●"
                ),
            },
            (5, 3): {
                "file": "icon_2.png",
                "target": "echo",
                "message": (
                    "--------------------------------------------\n"
                    "〇扇踏む(氷) |　●両方踏む\n"
                    "〇　　　　　|　〇\n"
                    "--------------------------------------------\n"
                    "〇踏まない 　|　●たて踏む(雷)\n"
                    "●　　　　　|　●"
                ),
            },
            (5, 4): {
                "file": "icon_1.png",
                "target": "echo",
                "message": (
                    "--------------------------------------------\n"
                    "〇たて踏む(雷)  |　●踏まない\n"
                    "〇　　　　　　|　〇\n"
                    "--------------------------------------------\n"
                    "〇両方踏む　　|　●扇踏む(氷) \n"
                    "●　　　　　　|　●"
                ),
            },
            (5, 5): {
                "file": "icon_0.png",
                "target": "echo",
                "message": (
                    "--------------------------------------------\n"
                    "〇両方踏む　　|　●扇踏む(氷) \n"
                    "〇　　　　　　|　〇\n"
                    "--------------------------------------------\n"
                    "〇たて踏む(雷)  |　●踏まない\n"
                    "●　　　　　　|　●"
                ),
            },
        }

        # 1920x810 / aspect2d 基準
        # 5x5グリッドを操作画面（左1440px）の右下へ寄せる。
        pixels_per_unit = 810 / 2.0
        window_aspect = 1920 / 810
        game_right_x = window_aspect * (2.0 * self.game_view_ratio - 1.0)

        previous_button_size_px = (0.072 * pixels_per_unit) + 2
        button_size_px = previous_button_size_px * 1.2
        gap_px = 2

        button_scale = button_size_px / pixels_per_unit

        step_x = (button_size_px + gap_px) / pixels_per_unit
        step_z = (button_size_px + gap_px) / pixels_per_unit

        margin_px = 8
        margin_unit = margin_px / pixels_per_unit
        half = button_scale / 2.0

        rightmost_center_x = game_right_x - margin_unit - half
        start_x = rightmost_center_x - step_x * 4

        bottom_center_z = -1.0 + margin_unit + half
        start_z = bottom_center_z + step_z * 4

        for row in range(5):
            row_buttons = []

            for col in range(5):
                x = start_x + col * step_x
                z = start_z - row * step_z

                # Excel側は1始まり
                macro_row = row + 1
                macro_col = col + 1
                macro = self.macro_defs.get((macro_row, macro_col))

                button_kwargs = {
                    "parent": self.aspect2d,
                    "text": "",
                    "pos": (x, 0, z),
                    "scale": button_scale,
                    "frameSize": (-0.50, 0.50, -0.50, 0.50),
                    "frameColor": (0.12, 0.12, 0.12, 0.88),
                    "relief": DGG.RIDGE,
                    "borderWidth": (0.08, 0.08),
                    "command": self.on_action_button_pressed,
                    "extraArgs": [macro_row, macro_col],
                }

                # 定義済みマクロは画像をボタン全面へ表示する。
                if macro is not None:
                    image_path = os.path.join(
                        self.macro_icon_dir,
                        macro["file"],
                    )

                    if os.path.exists(image_path):
                        button_kwargs["image"] = Filename.fromOsSpecific(
                            image_path
                        ).getFullpath()

                        # DirectButton自体をbutton_scaleで縮小しているため、
                        # ローカル座標では約1.0でボタン全面に合う。
                        button_kwargs["image_scale"] = (0.48, 1.0, 0.48)
                        button_kwargs["image_pos"] = (0, 0, 0)

                button = DirectButton(**button_kwargs)

                row_buttons.append(button)

            self.action_buttons.append(row_buttons)

    def execute_macro(self, row, col):
        """
        「マクロ一覧」で定義されたマクロ処理を実行する。

        row / col はExcelと同じ1始まり。
        手動クリックだけでなく、タイムライン補助からもこの関数を呼ぶことで、
        エコー/PTチャット表示と頭上マーカー処理を共通化する。
        """
        macro = self.macro_defs.get((row, col))

        # 未定義セルは何もしない。
        if macro is None:
            return

        target = macro["target"]
        message = macro["message"]

        if target == "echo":
            self.append_echo_text(message)

        elif target == "pt":
            self.append_pt_chat_text(message)

        # 頭上マーカーはチャット出力とは独立して実行する。
        marker = macro.get("marker")
        if marker is not None:
            self.set_player_head_marker(marker)

    def on_action_button_pressed(self, row, col):
        """
        マクロボタンを手動で押したときの入口。
        実際の処理は execute_macro() に集約する。
        """
        self.execute_macro(row, col)

    def setup_left_log_boxes(self):
        """
        左側にエコー / PTチャット風のログ表示欄を作る。

        ・上段: 半透明黒背景 + 黄色文字
        ・下段: 半透明黒背景 + 水色文字
        ・各8行まで保持
        ・append_echo_text() / append_pt_chat_text() で後から追記可能
        """

        self.echo_lines = []
        self.pt_chat_lines = []

        # 左下付近に配置
        left_x = -2.34
        box_width = 0.84
        # 8行を確実に表示できる高さ。
        box_height = 0.40

        # -------- エコー --------
        self.echo_box = DirectFrame(
            parent=self.aspect2d,
            pos=(0, 0, 0),
            frameSize=(
                left_x,
                left_x + box_width,
                -0.16 - box_height,
                -0.16,
            ),
            frameColor=(0.0, 0.0, 0.0, 0.45),
            relief=DGG.FLAT,
        )

        self.echo_text = OnscreenText(
            parent=self.aspect2d,
            text="\n".join(self.echo_lines),
            pos=(left_x + 0.045, -0.195),
            scale=0.034,
            align=TextNode.ALeft,
            fg=(1.0, 1.0, 0.0, 1.0),
            font=self.gui_font,
            mayChange=True,
        )

        # -------- PTチャット --------
        pt_top = -0.57

        self.pt_chat_box = DirectFrame(
            parent=self.aspect2d,
            pos=(0, 0, 0),
            frameSize=(
                left_x,
                left_x + box_width,
                pt_top - box_height,
                pt_top,
            ),
            frameColor=(0.0, 0.0, 0.0, 0.45),
            relief=DGG.FLAT,
        )

        self.pt_chat_text = OnscreenText(
            parent=self.aspect2d,
            text="\n".join(self.pt_chat_lines),
            pos=(left_x + 0.045, pt_top - 0.04),
            scale=0.034,
            align=TextNode.ALeft,
            fg=(0.0, 1.0, 1.0, 1.0),
            font=self.gui_font,
            mayChange=True,
        )

        # タイムラインのチャット補助から確実に参照できるよう、
        # 初期状態でも表示Node自体はshowのままにする。
        self.pt_chat_box.show()
        self.pt_chat_text.show()

    def append_echo_text(self, message):
        """
        エコー欄へ文字を追加する。

        「メッセージ数」ではなく実際の改行後の行数で管理し、
        常に最新8行だけを表示する。
        複数行マクロを実行した場合も、古い行が上へ押し出される。
        """
        new_lines = str(message).splitlines()

        # 空文字列でも1行として扱いたい場合に備える
        if not new_lines:
            new_lines = [""]

        self.echo_lines.extend(new_lines)
        self.echo_lines = self.echo_lines[-8:]
        self.echo_text.setText("\n".join(self.echo_lines))

    def append_pt_chat_assist(self, option_key, message):
        """
        チャット補助用の共通処理。

        option_key に対応するGUIチェックがONのときだけ、
        PTチャット欄へ message を追記する。

        PTチャット欄が何らかの理由で非表示になっていても、
        補助チャットを出すタイミングで明示的に再表示する。
        """
        enabled = bool(self.gui_flags.get(option_key, False))

        # 動作確認しやすいようコンソールにも状態を出す。
        print(
            f"[chat assist] option={option_key} "
            f"enabled={enabled} message={message}"
        )

        if not enabled:
            return

        if hasattr(self, "pt_chat_box"):
            self.pt_chat_box.show()
        if hasattr(self, "pt_chat_text"):
            self.pt_chat_text.show()

        self.append_pt_chat_text(message)

    def append_pt_chat_text(self, message):
        """
        PTチャット欄へ文字を追加する。

        「メッセージ数」ではなく実際の改行後の行数で管理し、
        常に最新8行だけを表示する。
        複数行マクロを実行した場合も、古い行が上へ押し出される。
        """
        new_lines = str(message).splitlines()

        if not new_lines:
            new_lines = [""]

        self.pt_chat_lines.extend(new_lines)
        self.pt_chat_lines = self.pt_chat_lines[-8:]
        self.pt_chat_text.setText("\n".join(self.pt_chat_lines))

    def clear_echo_text(self):
        self.echo_lines = []
        self.echo_text.setText("")

    def clear_pt_chat_text(self):
        self.pt_chat_lines = []
        self.pt_chat_text.setText("")

    def setup_gui(self):
        """
        右480pxの設定GUIを作成する。

        チェック項目は self.gui_flags に bool で保持する。
        音声・テキスト表示などの実処理は、今後のギミック実装側から
        self.gui_flags["キー名"] を参照して制御できる。
        """

        # --- 日本語フォント ---
        self.gui_font = None
        self.gui_font = self.loader.loadFont(
            Filename.fromOsSpecific(r"C:\Windows\Fonts\meiryo.ttc").getFullpath()
        )

        # 1920x810、右480px固定を前提にした aspect2d 上の座標。
        # 1600x675と同じ縦横比なので、GUIの相対配置はそのまま維持される。
        # aspect2d の横幅はウィンドウ縦横比に応じて -aspect ～ +aspect。
        window_aspect = 1920 / 810
        gui_left = window_aspect * (2.0 * self.game_view_ratio - 1.0)
        gui_right = window_aspect

        # 右480px領域の薄い灰色背景。
        # GUI本体と同じ aspect2d に置くため、描画順でGUIが消えない。
        self.gui_background = DirectFrame(
            parent=self.aspect2d,
            pos=(0, 0, 0),
            frameSize=(gui_left, gui_right, -1.0, 1.0),
            frameColor=(0.82, 0.82, 0.82, 1.0),
            relief=DGG.FLAT,
            sortOrder=-100,
        )

        # 右GUIの左端から少し余白を取る
        self.gui_x = gui_left + 0.055

        # GUIの状態
        self.gui_flags = {
            "same_pattern": False,

            # コール補助
            "call_gc_floor": True,
            "call_exdeath_truth": True,
            "call_beam_truth": True,
            "call_gaze": True,
            "call_fire_tsunami": True,
            "call_magic_charge_floor": True,
            "call_magic_out": True,

            # チャット補助
            "chat_gaze": True,
            "chat_fire_tsunami": True,
            "chat_charge_floor": False,

            # 個人デバフ補助
            "personal_spread_stack": False,
            "personal_acceleration": False,
        }

        self.gui_checkboxes = {}
        self.volume = 50.0

        # ---------- 上段 ----------
        self.reset_button = DirectButton(
            parent=self.aspect2d,
            text="Reset",
            text_scale=0.48,
            scale=0.10,
            pos=(self.gui_x + 0.08, 0, 0.92),
            frameSize=(-0.72, 0.72, -0.38, 0.38),
            frameColor=(0.78, 0.78, 0.78, 1),
            relief=DGG.RAISED,
            command=self.reset_simulator,
        )
        if self.gui_font is not None:
            self.reset_button["text_font"] = self.gui_font

        self.add_gui_checkbox(
            "same_pattern",
            "同じパターンを引き継ぐ",
            self.gui_x + 0.18,
            0.91,
        )

        self.add_gui_text("volume", self.gui_x, 0.80, scale=0.040)

        self.volume_slider = DirectSlider(
            parent=self.aspect2d,
            range=(0, 100),
            value=self.volume,
            pageSize=5,
            scale=0.32,
            pos=(self.gui_x + 0.62, 0, 0.79),
            command=self.on_volume_changed,
        )

        self.volume_value_text = self.add_gui_text(
            "50",
            self.gui_x + 1.00,
            0.80,
            scale=0.036,
            may_change=True,
        )

        # ---------- コール補助 ----------
        y = 0.68
        self.add_gui_text("★コール補助★", self.gui_x, y, scale=0.043)
        y -= 0.085

        call_items = [
            ("call_gc_floor", "GC床"),
            ("call_exdeath_truth", "エクスデス真偽"),
            ("call_beam_truth", "ビーム真偽"),
            ("call_gaze", "視線（発動時）"),
            ("call_fire_tsunami", "炎つなみ（発動時）"),
            ("call_magic_charge_floor", "マジックチャージ床"),
            ("call_magic_out", "マジックアウト"),
        ]
        for key, label in call_items:
            self.add_gui_checkbox(key, label, self.gui_x, y)
            y -= 0.072

        # ---------- チャット補助 ----------
        y -= 0.035
        self.add_gui_text("★チャット補助★", self.gui_x, y, scale=0.043)
        y -= 0.085

        chat_items = [
            ("chat_gaze", "視線"),
            ("chat_fire_tsunami", "炎つなみ"),
            ("chat_charge_floor", "チャージ床"),
        ]
        for key, label in chat_items:
            self.add_gui_checkbox(key, label, self.gui_x, y)
            y -= 0.072

        # ---------- 個人デバフ補助 ----------
        y -= 0.035
        self.add_gui_text("★個人デバフ補助★", self.gui_x, y, scale=0.043)
        y -= 0.085

        personal_items = [
            ("personal_spread_stack", "散会、頭割り"),
            ("personal_acceleration", "加速度"),
        ]
        for key, label in personal_items:
            self.add_gui_checkbox(key, label, self.gui_x, y)
            y -= 0.072

        # ---------- ミス内容 ----------
        # 画像のような枠だけ用意し、初期値は空白。
        self.miss_box = DirectFrame(
            parent=self.aspect2d,
            pos=(self.gui_x + 0.02, 0, -0.93),
            frameSize=(0, 1.02, 0, 0.40),
            frameColor=(0.82, 0.82, 0.82, 1),
            relief=DGG.GROOVE,
            borderWidth=(0.006, 0.006),
        )

        self.miss_text = OnscreenText(
            parent=self.aspect2d,
            text="",
            pos=(self.gui_x + 0.05, -0.58),
            scale=0.037,
            align=TextNode.ALeft,
            fg=(0.05, 0.05, 0.05, 1),
            font=self.gui_font,
            mayChange=True,
        )
        # ミス内容は6行の追記ログとして管理する。
        self.miss_lines = []

        # 起動時のミス内容は空欄。
        self.clear_miss_text()

    def add_gui_text(self, text, x, y, scale=0.040, may_change=False):
        """右GUI用の左寄せテキストを追加する。"""
        node = OnscreenText(
            parent=self.aspect2d,
            text=text,
            pos=(x, y),
            scale=scale,
            align=TextNode.ALeft,
            fg=(0.05, 0.05, 0.05, 1),
            font=self.gui_font,
            mayChange=may_change,
        )
        return node

    def add_gui_checkbox(self, key, label, x, y):
        """
        小さなDirectButtonをチェックボックスとして使用する。
        状態は self.gui_flags[key] に保持する。
        """
        box = DirectButton(
            parent=self.aspect2d,
            text="",
            pos=(x + 0.026, 0, y + 0.004),
            scale=0.058,
            frameSize=(-0.34, 0.34, -0.34, 0.34),
            frameColor=(0.88, 0.88, 0.88, 1),
            relief=DGG.SUNKEN,
            command=self.toggle_gui_checkbox,
            extraArgs=[key],
        )
        if self.gui_font is not None:
            box["text_font"] = self.gui_font

        label_node = self.add_gui_text(
            label,
            x + 0.085,
            y,
            scale=0.039,
        )

        self.gui_checkboxes[key] = {
            "button": box,
            "label": label_node,
        }
        self.update_gui_checkbox_visual(key)

    def toggle_gui_checkbox(self, key):
        self.gui_flags[key] = not self.gui_flags[key]
        self.update_gui_checkbox_visual(key)

    def update_gui_checkbox_visual(self, key):
        button = self.gui_checkboxes[key]["button"]
        if self.gui_flags[key]:
            button["text"] = "✓"
            button["text_scale"] = 0.95
            button["frameColor"] = (0.72, 0.82, 0.72, 1)
        else:
            button["text"] = ""
            button["frameColor"] = (0.88, 0.88, 0.88, 1)

    def on_volume_changed(self):
        """スライダー値を0～100で保持し、補助音声にも反映する。"""
        self.volume = float(self.volume_slider["value"])
        self.volume_value_text.setText(str(int(round(self.volume))))

        if hasattr(self, "supplemental_sound_cache"):
            volume = max(0.0, min(1.0, self.volume / 100.0))
            for sound in self.supplemental_sound_cache.values():
                if sound is not None:
                    sound.setVolume(volume)

    def setup_activation_effects(self):
        """
        判定タイミングを視覚的に知らせる簡易エフェクトを作成する。

        ・早/遅デバフ：
            自キャラ中心に白リングを約1秒表示。
            少し拡大しながら薄くなって消える。

        ・視線：
            左側ゲーム描画領域だけを半透明の白で短くフラッシュ。

        成功/失敗とは独立して必ず表示する。
        """
        # ---------- デバフ発動リング ----------
        self.activation_ring_duration = 1.00
        self.activation_ring_elapsed = 0.0
        self.activation_ring_active = False

        ls = LineSegs("activation_white_ring")
        ls.setThickness(4.0)
        ls.setColor(1.0, 1.0, 1.0, 1.0)

        segments = 96
        base_radius = 1.0
        for i in range(segments + 1):
            t = (2.0 * math.pi * i) / segments
            x = math.cos(t) * base_radius
            y = math.sin(t) * base_radius
            if i == 0:
                ls.moveTo(x, y, 0.0)
            else:
                ls.drawTo(x, y, 0.0)

        self.activation_ring = self.render.attachNewNode(ls.create())
        self.activation_ring.setTransparency(TransparencyAttrib.MAlpha)
        self.activation_ring.setDepthWrite(False)
        self.activation_ring.setBin("transparent", 80)
        self.activation_ring.hide()

        # ---------- 視線フラッシュ ----------
        self.gaze_flash_duration = 0.50
        self.gaze_flash_elapsed = 0.0
        self.gaze_flash_active = False

        window_aspect = 1920 / 810
        game_left = -window_aspect
        game_right = window_aspect * (2.0 * self.game_view_ratio - 1.0)

        self.gaze_flash = DirectFrame(
            parent=self.aspect2d,
            frameSize=(game_left, game_right, -1.0, 1.0),
            frameColor=(1.0, 1.0, 1.0, 0.0),
            relief=DGG.FLAT,
            sortOrder=1000,
        )
        self.gaze_flash.setTransparency(TransparencyAttrib.MAlpha)
        self.gaze_flash.hide()

    def show_activation_ring(self):
        """自キャラ中心の白リング発動エフェクトを開始する。"""
        if not hasattr(self, "activation_ring"):
            return

        self.activation_ring_elapsed = 0.0
        self.activation_ring_active = True

        pos = self.player.getPos(self.render)
        self.activation_ring.setPos(pos.x, pos.y, 0.14)
        self.activation_ring.setScale(0.85)
        self.activation_ring.setColorScale(1.0, 1.0, 1.0, 0.95)
        self.activation_ring.show()

    def show_gaze_flash(self):
        """ゲーム画面を短く白くフラッシュさせる。"""
        if not hasattr(self, "gaze_flash"):
            return

        self.gaze_flash_elapsed = 0.0
        self.gaze_flash_active = True
        self.gaze_flash["frameColor"] = (1.0, 1.0, 1.0, 0.42)
        self.gaze_flash.show()

    def update_activation_effects(self, dt):
        """白リングと視線フラッシュの短いアニメーションを更新する。"""
        # ---------- 白リング ----------
        if getattr(self, "activation_ring_active", False):
            self.activation_ring_elapsed += dt
            p = min(
                1.0,
                self.activation_ring_elapsed / self.activation_ring_duration,
            )

            # プレイヤー移動中でも中心へ追従させる。
            pos = self.player.getPos(self.render)
            self.activation_ring.setPos(pos.x, pos.y, 0.14)

            # 0.85倍 -> 2.35倍へ拡大しながらフェードアウト。
            scale = 0.85 + (2.35 - 0.85) * p
            alpha = max(0.0, 0.95 * (1.0 - p))
            self.activation_ring.setScale(scale)
            self.activation_ring.setColorScale(1.0, 1.0, 1.0, alpha)

            if p >= 1.0:
                self.activation_ring_active = False
                self.activation_ring.hide()

        # ---------- 視線フラッシュ ----------
        if getattr(self, "gaze_flash_active", False):
            self.gaze_flash_elapsed += dt
            p = min(
                1.0,
                self.gaze_flash_elapsed / self.gaze_flash_duration,
            )

            # 最初を最も明るくして、そのまま素早く消す。
            alpha = max(0.0, 0.42 * (1.0 - p))
            self.gaze_flash["frameColor"] = (1.0, 1.0, 1.0, alpha)

            if p >= 1.0:
                self.gaze_flash_active = False
                self.gaze_flash.hide()

    def clear_activation_effects(self):
        """Reset時に途中の発動エフェクトを消す。"""
        self.activation_ring_active = False
        self.gaze_flash_active = False

        if hasattr(self, "activation_ring"):
            self.activation_ring.hide()
        if hasattr(self, "gaze_flash"):
            self.gaze_flash.hide()

    def setup_player_head_marker(self):
        """
        プレイヤー頭上に表示する番号マーカーを初期化する。

        ・render配下に置き、update_player_head_marker_position() でプレイヤーへ追従
        ・カメラへ常に正面を向くビルボード表示
        ・set_player_head_marker("1" / "2") で表示・切り替え
        ・clear_player_head_marker() で非表示
        ・マクロだけでなく、後から補助チェック処理からも直接呼び出せる
        """
        self.player_head_marker_value = None

        self.player_head_marker_root = self.render.attachNewNode(
            "player_head_marker_root"
        )

        # プレイヤーから少し上へ浮かせる。
        # MISSは画面固定UIなので、こちらはワールド空間でプレイヤーに追従する。
        self.player_head_marker_height = 0.95

        # カメラ方向へ常に正面を向ける。
        self.player_head_marker_root.setBillboardPointEye()

        # FF14のアタックマーカー風に、黄色～橙色の六角形の輪郭を描く。
        radius = 0.40
        line = LineSegs("player_head_marker_hex")
        line.setThickness(4.0)
        line.setColor(1.0, 0.78, 0.18, 1.0)

        points = []
        # 上下が尖りすぎない六角形。
        for i in range(6):
            angle = math.radians(90.0 + i * 60.0)
            x = math.cos(angle) * radius
            z = math.sin(angle) * radius
            points.append((x, 0.0, z))

        line.moveTo(*points[0])
        for p in points[1:]:
            line.drawTo(*p)
        line.drawTo(*points[0])

        hex_node = self.player_head_marker_root.attachNewNode(line.create())
        hex_node.setTransparency(TransparencyAttrib.MAlpha)

        # 番号本体
        text_node = TextNode("player_head_marker_text")
        text_node.setAlign(TextNode.ACenter)
        text_node.setText("")
        text_node.setTextColor(1.0, 0.92, 0.55, 1.0)
        if self.gui_font is not None:
            text_node.setFont(self.gui_font)

        self.player_head_marker_text_node = text_node
        self.player_head_marker_text = (
            self.player_head_marker_root.attachNewNode(text_node)
        )
        self.player_head_marker_text.setScale(0.52)
        self.player_head_marker_text.setPos(0.0, -0.01, -0.18)

        self.update_player_head_marker_position()
        self.player_head_marker_root.hide()

    def update_player_head_marker_position(self):
        """頭上マーカーを現在のプレイヤー位置へ追従させる。"""
        if not hasattr(self, "player_head_marker_root"):
            return
        if not hasattr(self, "player"):
            return

        pos = self.player.getPos(self.render)
        self.player_head_marker_root.setPos(
            pos.x,
            pos.y,
            pos.z + self.player_head_marker_height,
        )

    def set_player_head_marker(self, marker):
        """
        プレイヤー頭上マーカーを表示する。

        marker:
            "1" / 1 -> 1マーカー
            "2" / 2 -> 2マーカー

        同じ関数を、今後の補助チェック処理からも使用できる。
        """
        marker = str(marker)

        if marker not in ("1", "2"):
            return

        if not hasattr(self, "player_head_marker_root"):
            return

        self.player_head_marker_value = marker
        self.player_head_marker_text_node.setText(marker)
        self.update_player_head_marker_position()
        self.player_head_marker_root.show()

    def clear_player_head_marker(self):
        """プレイヤー頭上マーカーを消す。"""
        self.player_head_marker_value = None

        if hasattr(self, "player_head_marker_text_node"):
            self.player_head_marker_text_node.setText("")

        if hasattr(self, "player_head_marker_root"):
            self.player_head_marker_root.hide()

    def setup_miss_overlay(self):
        """
        ミス発生時に一瞬表示する「MISS」通知を作成する。

        ・ゲーム描画領域（左75%）の中央より少し上
        ・オレンジ色 + 黒い影
        ・通常時は非表示
        ・set_miss_text() が呼ばれるたびに約0.8秒表示
        """
        # aspect2d全体の横幅から、左75%のゲーム領域中央を求める。
        window_aspect = 1920 / 810
        game_center_x = window_aspect * (self.game_view_ratio - 1.0)

        self.miss_overlay_duration = 0.8
        self.miss_overlay_task_name = "hide_miss_overlay"

        self.miss_overlay = OnscreenText(
            parent=self.aspect2d,
            text="MISS",
            pos=(game_center_x, -0.15),
            scale=0.07,
            align=TextNode.ACenter,
            fg=(1.0, 0.48, 0.06, 1.0),
            font=self.gui_font,
            shadow=(0.0, 0.0, 0.0, 0.95),
            shadowOffset=(0.004, 0.004),
            mayChange=False,
        )
        self.miss_overlay.hide()

    def show_miss_overlay(self):
        """MISS通知を表示し、約0.8秒後に自動で消す。"""
        if not hasattr(self, "miss_overlay"):
            return

        # 連続してミスした場合は、最後のミスから0.8秒表示を延長する。
        self.taskMgr.remove(self.miss_overlay_task_name)
        self.miss_overlay.show()
        self.taskMgr.doMethodLater(
            self.miss_overlay_duration,
            self._hide_miss_overlay_task,
            self.miss_overlay_task_name,
        )

    def _hide_miss_overlay_task(self, task):
        if hasattr(self, "miss_overlay"):
            self.miss_overlay.hide()
        return Task.done

    def set_miss_text(self, message):
        """
        ギミック失敗時にミス内容を追記する。

        ・既存内容を上書きせず、次の行へ追加
        ・最大6行を保持
        ・7件目以降は最も古い行を押し出す
        """
        if not hasattr(self, "miss_lines"):
            self.miss_lines = []

        for line in str(message).splitlines():
            if line:
                self.miss_lines.append(line)

        self.miss_lines = self.miss_lines[-6:]
        self.miss_text.setText("\n".join(self.miss_lines))

        # 詳細ログとは別に、画面中央付近へ瞬間的なミス通知を出す。
        self.show_miss_overlay()

    def clear_miss_text(self):
        """ミス内容を全てクリアする。"""
        self.miss_lines = []
        self.miss_text.setText("")

        # Reset等で残っているMISS通知も消す。
        if hasattr(self, "miss_overlay_task_name"):
            self.taskMgr.remove(self.miss_overlay_task_name)
        if hasattr(self, "miss_overlay"):
            self.miss_overlay.hide()

    def reset_simulator(self):
        """
        シミュレータを0秒へ戻す。

        「同じパターンを引き継ぐ」がOFF:
            patternを全て再抽選
        ON:
            現在のpatternをそのまま再利用
        """
        # --- パターン ---
        if not self.gui_flags.get("same_pattern", False):
            self.generate_pattern()
        else:
            # patternを維持したまま、derivedを必ず同期する。
            self.generate_derived()

        # --- 補助音声 ---
        if hasattr(self, "supplemental_sound_cache"):
            self.stop_supplemental_audio()

        if hasattr(self, "positional_sound_cache"):
            for sound in self.positional_sound_cache.values():
                if sound is not None:
                    sound.stop()

        # --- プレイヤー ---
        self.player.setPos(0, 0, 0.18)
        self.set_player_heading(0.0)

        # --- カメラ ---
        self.camera_yaw = 0
        self.camera_pitch = 10.0

        # --- 入力状態 ---
        for key in self.keys:
            self.keys[key] = False
        self.player_is_moving = False
        self.mouse_look_active = False

        # --- ログ / ミス ---
        self.clear_echo_text()
        self.clear_pt_chat_text()
        self.clear_miss_text()
        self.clear_activation_effects()
        self.clear_player_head_marker()

        # --- デバフ ---
        self.clear_all_debuffs()

        # --- 詠唱 ---
        for enemy_id in ("kefka", "chaos", "exdeath"):
            self.stop_enemy_cast(enemy_id, hide=True)

        # --- 敵・各種エフェクトを初期状態へ ---
        self.apply_timeline_initial_state()

        self.hide_kefka_radial_aoe()
        self.hide_kefka_floor_aoe()
        self.hide_kefka_truth_effect()
        self.hide_chaos_truth_effect()
        self.hide_exdeath_truth_effect()
        self.hide_exdeath_beam_effect()
        self.hide_exdeath_beam_floor()

        # デバッグ用頭割り範囲はReset後も表示ONを維持。
        if hasattr(self, "stack_area_debug_root"):
            self.stack_area_debug_visible = False

        # エクスデスの初期位置は既存仕様どおりNEへ戻す。
        self.set_exdeath_direction("NE")
        if hasattr(self, "stack_area_debug_root"):
            self.update_stack_area_debug()

        # pattern依存のビーム色なども、新patternへ同期だけしておく。
        self.set_exdeath_beam_color(self.pattern["beam_color"])
        self.set_exdeath_beam_floor(
            beam_color=self.pattern["beam_color"],
            beam_truth=self.pattern["beam_truth"],
            visible=False,
        )

        # --- タイムラインを0秒から再開 ---
        self.start_timeline()



    def setup_kefka_truth_effect(self):
        """
        ケフカ周囲の真偽エフェクトを作成する。

        上段リング: 紫固定（雷床）
        下段リング: 青固定（氷床）

        各リングに2個の玉を180度反対位置で配置し、
        上段は時計回り、下段は反時計回りに公転させる。

        真  : くすんだ水色の玉
        偽  : 赤い玉 + 黄色の「?」

        初期表示の真偽や表示有無は set_kefka_truth_effect() で設定する。
        """
        self.kefka_truth_effect = {
            "upper": {
                "visible": False,
                "truth": True,
                "angle": 0.0,
                "speed": 55.0,   # deg/sec（時計回り）
            },
            "lower": {
                "visible": False,
                "truth": True,
                "angle": 180.0,
                "speed": -55.0,  # deg/sec（反時計回り）
            },
        }

        # ケフカ本体のサイズに合わせた仮パラメータ
        orbit_radius = 1.45
        upper_z = 1.48
        lower_z = 0.88
        orb_scale = 0.18

        self.kefka_effect_params = {
            "orbit_radius": orbit_radius,
            "upper_z": upper_z,
            "lower_z": lower_z,
            "orb_scale": orb_scale,
        }

        # ケフカに追従するルート
        self.kefka_effect_root = self.enemies["kefka"].attachNewNode(
            "kefka_truth_effect_root"
        )

        # --- 上段：雷リング（紫固定） ---
        self.kefka_upper_ring = self.create_orbit_ring(
            name="kefka_upper_thunder_ring",
            radius=orbit_radius,
            z=upper_z,
            color=(0.62, 0.34, 0.88, 0.95),
            thickness=3.0,
        )
        self.kefka_upper_ring.reparentTo(self.kefka_effect_root)

        # --- 下段：氷リング（青固定） ---
        self.kefka_lower_ring = self.create_orbit_ring(
            name="kefka_lower_ice_ring",
            radius=orbit_radius,
            z=lower_z,
            color=(0.30, 0.70, 1.00, 0.95),
            thickness=3.0,
        )
        self.kefka_lower_ring.reparentTo(self.kefka_effect_root)

        self.kefka_truth_orbs = {
            "upper": [],
            "lower": [],
        }

        for layer in ("upper", "lower"):
            for index in range(2):
                orb_root = self.kefka_effect_root.attachNewNode(
                    f"kefka_{layer}_orb_{index}"
                )

                # 玉本体
                orb = self.loader.loadModel("models/misc/sphere")
                orb.reparentTo(orb_root)
                orb.setScale(orb_scale)
                orb.setColor(0.30, 0.46, 0.56, 1.0)

                # 偽のときだけ使う「?」
                tn = TextNode(f"kefka_{layer}_question_{index}")
                tn.setText("?")
                tn.setAlign(TextNode.ACenter)
                tn.setTextColor(1.0, 0.90, 0.10, 1.0)

                question = orb_root.attachNewNode(tn)
                question.setBillboardPointEye()
                # 位置は update_kefka_truth_effect() 内で、
                # 玉からカメラ方向へ少し手前に出す。
                question.setPos(0, 0, 0)
                question.setScale(0.30)
                question.hide()

                self.kefka_truth_orbs[layer].append({
                    "root": orb_root,
                    "orb": orb,
                    "question": question,
                })

        # 起動直後は一旦非表示。
        self.hide_kefka_truth_effect()

    def create_orbit_ring(self, name, radius, z, color, thickness=3.0, segments=96):
        """ケフカ周囲に使う水平リングを作成する。"""
        ls = LineSegs(name)
        ls.setThickness(thickness)
        ls.setColor(*color)

        for i in range(segments + 1):
            t = 2.0 * math.pi * i / segments
            x = math.cos(t) * radius
            y = math.sin(t) * radius

            if i == 0:
                ls.moveTo(x, y, z)
            else:
                ls.drawTo(x, y, z)

        node = self.render.attachNewNode(ls.create())
        node.setTransparency(TransparencyAttrib.MAlpha)
        return node

    def set_kefka_truth_effect(
        self,
        upper_visible=True,
        lower_visible=True,
        upper_truth=True,
        lower_truth=True,
    ):
        """
        ケフカの上下リング表示と真偽をまとめて設定する。

        upper: 雷床（紫リング）
        lower: 氷床（青リング）
        truth=True  -> 青玉
        truth=False -> 赤玉 + 黄色「?」
        """
        self.kefka_effect_root.show()
        self.kefka_truth_effect["upper"]["visible"] = upper_visible
        self.kefka_truth_effect["lower"]["visible"] = lower_visible
        self.kefka_truth_effect["upper"]["truth"] = upper_truth
        self.kefka_truth_effect["lower"]["truth"] = lower_truth

        self._apply_kefka_truth_visual("upper")
        self._apply_kefka_truth_visual("lower")
        self._apply_kefka_layer_visibility("upper")
        self._apply_kefka_layer_visibility("lower")

    def set_kefka_layer_truth(self, layer, truth):
        """upper / lower の真偽だけを変更する。"""
        if layer not in ("upper", "lower"):
            raise ValueError("layer must be 'upper' or 'lower'")

        self.kefka_truth_effect[layer]["truth"] = bool(truth)
        self._apply_kefka_truth_visual(layer)

    def set_kefka_layer_visible(self, layer, visible):
        """upper / lower の表示有無だけを変更する。"""
        if layer not in ("upper", "lower"):
            raise ValueError("layer must be 'upper' or 'lower'")

        if visible:
            self.kefka_effect_root.show()

        self.kefka_truth_effect[layer]["visible"] = bool(visible)
        self._apply_kefka_layer_visibility(layer)

    def show_kefka_truth_effect(self):
        """上下両方を表示する。"""
        self.set_kefka_layer_visible("upper", True)
        self.set_kefka_layer_visible("lower", True)

    def hide_kefka_truth_effect(self):
        """上下両方を非表示にする。"""
        self.set_kefka_layer_visible("upper", False)
        self.set_kefka_layer_visible("lower", False)

    def _apply_kefka_truth_visual(self, layer):
        truth = self.kefka_truth_effect[layer]["truth"]

        if truth:
            # 参考画像に寄せた、少しくすんだ青
            orb_color = (0.30, 0.46, 0.56, 1.0)
        else:
            # 鮮やかな赤ではなく、あずき色寄りのくすんだ赤
            orb_color = (0.50, 0.20, 0.24, 1.0)

        for orb_info in self.kefka_truth_orbs[layer]:
            orb_info["orb"].setColor(*orb_color)

            if truth:
                orb_info["question"].hide()
            else:
                orb_info["question"].show()

    def _apply_kefka_layer_visibility(self, layer):
        visible = self.kefka_truth_effect[layer]["visible"]

        ring = (
            self.kefka_upper_ring
            if layer == "upper"
            else self.kefka_lower_ring
        )

        if visible:
            ring.show()
            for orb_info in self.kefka_truth_orbs[layer]:
                orb_info["root"].show()
        else:
            ring.hide()
            for orb_info in self.kefka_truth_orbs[layer]:
                orb_info["root"].hide()

    def update_kefka_truth_effect(self, dt):
        """上下リング上の2個ずつの玉を公転させる。"""
        if not hasattr(self, "kefka_truth_effect"):
            return

        orbit_radius = self.kefka_effect_params["orbit_radius"]

        for layer in ("upper", "lower"):
            state = self.kefka_truth_effect[layer]

            if not state["visible"]:
                continue

            state["angle"] = (
                state["angle"] + state["speed"] * dt
            ) % 360.0

            z = (
                self.kefka_effect_params["upper_z"]
                if layer == "upper"
                else self.kefka_effect_params["lower_z"]
            )

            for index, orb_info in enumerate(
                self.kefka_truth_orbs[layer]
            ):
                angle = state["angle"] + index * 180.0
                rad = math.radians(angle)

                x = math.cos(rad) * orbit_radius
                y = math.sin(rad) * orbit_radius

                orb_info["root"].setPos(x, y, z)

                # 偽の「?」は、玉の中心からカメラ方向へ少しだけ手前に出す。
                # こうすることで、玉の上ではなく正面に貼り付いて見える。
                if not state["truth"]:
                    orb_world = orb_info["root"].getPos(self.render)
                    cam_world = self.camera.getPos(self.render)
                    to_camera = cam_world - orb_world

                    if to_camera.lengthSquared() > 0.0001:
                        to_camera.normalize()

                        # 玉の半径より少し手前へ。
                        question_world = orb_world + to_camera * 0.20

                        # 「？」を玉の中心より少し下へ
                        question_world.z -= 0.06

                        orb_info["question"].setPos(
                            self.render,
                            question_world.x,
                            question_world.y,
                            question_world.z,
                        )


    def setup_chaos_truth_effect(self):
        """
        カオス周囲の真偽エフェクトを作成する。

        ・リング線なし
        ・真偽の玉2個のみ
        ・2個は180度反対位置
        ・時計回りに公転
        ・真  : くすんだ青
        ・偽  : あずき色 + 黄色の「?」
        ・ケフカより大きい玉
        """
        self.chaos_truth_effect = {
            "visible": False,
            "truth": True,
            "angle": 0.0,
            "speed": -55.0,   # deg/sec（時計回り）
        }

        # カオス本体が大きいため、軌道半径・玉サイズもケフカより大きめ
        self.chaos_effect_params = {
            "orbit_radius": 2.05,
            "z": 1.32,
            "orb_scale": 0.30,
            "question_front_offset": 0.34,
            "question_z_offset": -0.07,
        }

        # カオス本体に追従するルート
        self.chaos_effect_root = self.enemies["chaos"].attachNewNode(
            "chaos_truth_effect_root"
        )

        self.chaos_truth_orbs = []

        for index in range(2):
            orb_root = self.chaos_effect_root.attachNewNode(
                f"chaos_truth_orb_{index}"
            )

            # 玉本体
            orb = self.loader.loadModel("models/misc/sphere")
            orb.reparentTo(orb_root)
            orb.setScale(self.chaos_effect_params["orb_scale"])
            orb.setColor(0.30, 0.46, 0.56, 1.0)

            # 偽のときだけ使う「?」
            tn = TextNode(f"chaos_question_{index}")
            tn.setText("?")
            tn.setAlign(TextNode.ACenter)
            tn.setTextColor(1.0, 0.90, 0.10, 1.0)

            question = orb_root.attachNewNode(tn)
            question.setBillboardPointEye()
            question.setPos(0, 0, 0)
            question.setScale(0.52)
            question.hide()

            self.chaos_truth_orbs.append({
                "root": orb_root,
                "orb": orb,
                "question": question,
            })

        self.hide_chaos_truth_effect()

    def set_chaos_truth_effect(self, visible=True, truth=True):
        """
        カオス真偽エフェクトの表示有無と真偽を設定する。

        truth=True  -> くすんだ青玉
        truth=False -> あずき色玉 + 黄色「?」
        """
        if visible:
            self.chaos_effect_root.show()

        self.chaos_truth_effect["visible"] = bool(visible)
        self.chaos_truth_effect["truth"] = bool(truth)

        self._apply_chaos_truth_visual()
        self._apply_chaos_visibility()

    def set_chaos_truth(self, truth):
        """カオスの真偽だけを変更する。"""
        self.chaos_truth_effect["truth"] = bool(truth)
        self._apply_chaos_truth_visual()

    def show_chaos_truth_effect(self):
        """カオス真偽エフェクトを表示する。"""
        self.chaos_effect_root.show()
        self.chaos_truth_effect["visible"] = True
        self._apply_chaos_visibility()

    def hide_chaos_truth_effect(self):
        """カオス真偽エフェクトを非表示にする。"""
        self.chaos_truth_effect["visible"] = False
        self._apply_chaos_visibility()

    def _apply_chaos_truth_visual(self):
        truth = self.chaos_truth_effect["truth"]

        if truth:
            orb_color = (0.30, 0.46, 0.56, 1.0)
        else:
            orb_color = (0.50, 0.20, 0.24, 1.0)

        for orb_info in self.chaos_truth_orbs:
            orb_info["orb"].setColor(*orb_color)

            if truth:
                orb_info["question"].hide()
            else:
                orb_info["question"].show()

    def _apply_chaos_visibility(self):
        visible = self.chaos_truth_effect["visible"]

        for orb_info in self.chaos_truth_orbs:
            if visible:
                orb_info["root"].show()
            else:
                orb_info["root"].hide()

    def update_chaos_truth_effect(self, dt):
        """カオス周囲の2個の玉を時計回りに公転させる。"""
        if not hasattr(self, "chaos_truth_effect"):
            return

        state = self.chaos_truth_effect

        if not state["visible"]:
            return

        state["angle"] = (
            state["angle"] + state["speed"] * dt
        ) % 360.0

        orbit_radius = self.chaos_effect_params["orbit_radius"]
        z = self.chaos_effect_params["z"]

        for index, orb_info in enumerate(self.chaos_truth_orbs):
            angle = state["angle"] + index * 180.0
            rad = math.radians(angle)

            x = math.cos(rad) * orbit_radius
            y = math.sin(rad) * orbit_radius

            orb_info["root"].setPos(x, y, z)

            # 偽の「?」は玉の正面（カメラ側）へ少し手前に出す。
            if not state["truth"]:
                orb_world = orb_info["root"].getPos(self.render)
                cam_world = self.camera.getPos(self.render)
                to_camera = cam_world - orb_world

                if to_camera.lengthSquared() > 0.0001:
                    to_camera.normalize()

                    question_world = (
                        orb_world
                        + to_camera * self.chaos_effect_params["question_front_offset"]
                    )
                    question_world.z += self.chaos_effect_params["question_z_offset"]

                    orb_info["question"].setPos(
                        self.render,
                        question_world.x,
                        question_world.y,
                        question_world.z,
                    )


    def setup_exdeath_beam_effect(self):
        """
        エクスデス左右に浮く青/紫の板状エフェクトを作成する。

        ・画像: ./img/beam/blue.png, purple.png
        ・位置はエクスデス本体に追従
        ・エクスデスが8方向へ移動しても、フィールド中央から見た
          「向かって左 / 向かって右」の関係を維持する
        ・カード面はカメラへ向け、画像の視認性を保つ
        """
        self.exdeath_beam_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "img",
            "beam",
        )

        self.exdeath_beam_state = {
            "visible": False,
            "color": "LEFTBLUE",
        }

        # 見た目調整用。画像の縦横比はPNGから自動取得するため、
        # 基本的には height と左右距離だけ調整すればよい。
        self.exdeath_beam_params = {
            "side_offset": 3.45,   # エクスデス中心から左右への距離
            "height": 3.20,        # 板画像の表示高さ
            "z": 2.15,             # 板中心の高さ
            "front_offset": 0.15,  # 中央側へ少し前出し
        }

        self.exdeath_beam_root = self.enemies["exdeath"].attachNewNode(
            "exdeath_beam_root"
        )

        self.exdeath_beam_cards = {}

        for side in ("left", "right"):
            card_root = self.exdeath_beam_root.attachNewNode(
                f"exdeath_beam_{side}_root"
            )

            cm = CardMaker(f"exdeath_beam_{side}_card")
            cm.setFrame(-0.5, 0.5, -0.5, 0.5)
            card = card_root.attachNewNode(cm.generate())

            card.setTransparency(TransparencyAttrib.MAlpha)
            card.setDepthWrite(False)
            card.setBin("transparent", 30)

            # 面だけは常にカメラ側を向く。
            card.setBillboardPointEye()

            self.exdeath_beam_cards[side] = {
                "root": card_root,
                "card": card,
                "filename": None,
            }

        self.update_exdeath_beam_orientation()
        self.hide_exdeath_beam_effect()

    def _load_exdeath_beam_texture(self, side, filename):
        """指定側のカードへbeam画像を設定し、画像比率を維持する。"""
        info = self.exdeath_beam_cards[side]
        path = os.path.join(self.exdeath_beam_dir, filename)

        if not os.path.exists(path):
            print(f"[beam] image not found: {path}")
            info["card"].hide()
            return

        texture = self.loader.loadTexture(
            Filename.fromOsSpecific(path).getFullpath()
        )
        if texture is None:
            print(f"[beam] could not load texture: {path}")
            info["card"].hide()
            return

        info["card"].setTexture(texture, 1)
        info["card"].setColor(1, 1, 1, 1)
        info["card"].show()
        info["filename"] = filename

        # 元画像の縦横比を維持。
        tex_w = max(1, texture.getXSize())
        tex_h = max(1, texture.getYSize())
        aspect = tex_w / tex_h

        height = self.exdeath_beam_params["height"]
        width = height * aspect
        info["card"].setScale(width, 1.0, height)

    def set_exdeath_beam_color(self, beam_color):
        """
        LEFTBLUE:
            フィールド中央からエクスデスを見て左=青 / 右=紫
        RIGHTBLUE:
            左=紫 / 右=青
        """
        beam_color = str(beam_color).upper()
        if beam_color not in ("LEFTBLUE", "RIGHTBLUE"):
            raise ValueError("beam_color must be LEFTBLUE or RIGHTBLUE")

        self.pattern["beam_color"] = beam_color
        self.exdeath_beam_state["color"] = beam_color

        if beam_color == "LEFTBLUE":
            left_file = "blue.png"
            right_file = "purple.png"
        else:
            left_file = "purple.png"
            right_file = "blue.png"

        self._load_exdeath_beam_texture("left", left_file)
        self._load_exdeath_beam_texture("right", right_file)
        self.update_exdeath_beam_orientation()
        self._apply_exdeath_beam_visibility()

    def show_exdeath_beam_effect(self):
        self.exdeath_beam_state["visible"] = True
        self._apply_exdeath_beam_visibility()

    def hide_exdeath_beam_effect(self):
        self.exdeath_beam_state["visible"] = False
        self._apply_exdeath_beam_visibility()

    def _apply_exdeath_beam_visibility(self):
        if not hasattr(self, "exdeath_beam_root"):
            return

        if self.exdeath_beam_state["visible"]:
            self.exdeath_beam_root.show()
        else:
            self.exdeath_beam_root.hide()

    def update_exdeath_beam_orientation(self):
        """
        エクスデスの現在方角に合わせて左右カードの位置を更新する。

        エクスデス→フィールド中央を正面方向とし、
        フィールド中央側からエクスデスを見たときの
        screen-left / screen-right を求める。
        """
        if not hasattr(self, "exdeath_beam_root"):
            return

        exdeath_pos = self.enemies["exdeath"].getPos(self.render)

        # エクスデスからフィールド中央への水平ベクトル
        forward = Vec2(-exdeath_pos.x, -exdeath_pos.y)
        if forward.lengthSquared() < 0.0001:
            forward = Vec2(0, -1)
        else:
            forward.normalize()

        # 中央からエクスデスを見た際の「向かって右」。
        # forward（敵から中央）に対して左手側が、観察者から見た右になる。
        view_right = Vec2(-forward.y, forward.x)

        side_offset = self.exdeath_beam_params["side_offset"]
        front_offset = self.exdeath_beam_params["front_offset"]
        z = self.exdeath_beam_params["z"]

        # 「向かって左」は view_right の逆。
        left_xy = (
            -view_right * side_offset
            + forward * front_offset
        )
        right_xy = (
            view_right * side_offset
            + forward * front_offset
        )

        self.exdeath_beam_cards["left"]["root"].setPos(
            left_xy.x, left_xy.y, z
        )
        self.exdeath_beam_cards["right"]["root"].setPos(
            right_xy.x, right_xy.y, z
        )

    def setup_exdeath_beam_floor(self):
        """
        エクスデス方向を正面として、フィールドを左右2色に分割する床表示。

        ・向かって左半分 / 右半分を青・紫で塗る
        ・中央には太い半透明の赤線
        ・beam_color と beam_truth から左右色を決める
        ・エクスデスが8方向へ移動した場合も、その方向へ追従して回転する
        ・Hit判定はここでは実装しない（見た目のみ）
        """
        self.exdeath_beam_floor_state = {
            "visible": False,
            "beam_color": "LEFTBLUE",
            "beam_truth": True,
        }

        self.exdeath_beam_floor_params = {
            "z": 0.045,
            "center_line_width": self.field_radius * 0.16,
            "blue_color": (0.12, 0.34, 0.92, 0.30),
            "purple_color": (0.58, 0.16, 0.72, 0.30),
            "red_color": (0.95, 0.10, 0.16, 0.38),
        }

        # ルートを中央に置き、ローカル+Yをエクスデス方向へ向ける。
        self.exdeath_beam_floor_root = self.render.attachNewNode(
            "exdeath_beam_floor_root"
        )
        self.exdeath_beam_floor_root.setPos(
            0, 0, self.exdeath_beam_floor_params["z"]
        )

        self.exdeath_beam_floor_node = None

        self._rebuild_exdeath_beam_floor()
        self.update_exdeath_beam_floor_orientation()
        self.hide_exdeath_beam_floor()

    def _create_exdeath_beam_floor_combined_geom(
        self,
        name,
        left_color,
        right_color,
        center_color,
        segments_per_half=64,
    ):
        """
        左半面・右半面・中央赤帯を、すべて1つのGeomにまとめて描画する。

        半透明の左右床を別Nodeにすると、視点によって描画順や深度判定が
        不安定になることがあるため、1Geomに統合する。
        """
        radius = self.field_radius
        half_line_width = (
            self.exdeath_beam_floor_params["center_line_width"] / 2.0
        )

        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(name, fmt, Geom.UHStatic)
        vw = GeomVertexWriter(vdata, "vertex")
        cw = GeomVertexWriter(vdata, "color")
        tris = GeomTriangles(Geom.UHStatic)

        vertex_index = 0

        def add_half_disc(start_deg, end_deg, color):
            nonlocal vertex_index

            center_index = vertex_index
            vw.addData3f(0.0, 0.0, 0.0)
            cw.addData4f(*color)
            vertex_index += 1

            arc_start = vertex_index

            for i in range(segments_per_half + 1):
                deg = (
                    start_deg
                    + (end_deg - start_deg) * i / segments_per_half
                )
                rad = math.radians(deg)
                x = math.cos(rad) * radius
                y = math.sin(rad) * radius
                vw.addData3f(x, y, 0.0)
                cw.addData4f(*color)
                vertex_index += 1

            for i in range(segments_per_half):
                tris.addVertices(
                    center_index,
                    arc_start + i,
                    arc_start + i + 1,
                )

        # ローカル+Yがエクスデス方向。
        # 左半面 = x <= 0、右半面 = x >= 0
        add_half_disc(90.0, 270.0, left_color)
        add_half_disc(-90.0, 90.0, right_color)

        # 中央赤帯
        half_len = math.sqrt(
            max(0.0, radius * radius - half_line_width * half_line_width)
        )
        base = vertex_index
        points = [
            (-half_line_width, -half_len, 0.0),
            ( half_line_width, -half_len, 0.0),
            ( half_line_width,  half_len, 0.0),
            (-half_line_width,  half_len, 0.0),
        ]
        for x, y, z in points:
            vw.addData3f(x, y, z)
            cw.addData4f(*center_color)
            vertex_index += 1

        tris.addVertices(base + 0, base + 1, base + 2)
        tris.addVertices(base + 0, base + 2, base + 3)

        geom = Geom(vdata)
        geom.addPrimitive(tris)

        gnode = GeomNode(name)
        gnode.addGeom(geom)

        node = self.exdeath_beam_floor_root.attachNewNode(gnode)
        node.setTransparency(TransparencyAttrib.MAlpha)
        node.setDepthWrite(False)
        node.setDepthOffset(1)
        node.setBin("transparent", 20)

        return node

    def _resolve_exdeath_beam_floor_colors(self, beam_color, beam_truth):
        """
        beam_color:
            LEFTBLUE  = 見た目上、向かって左が青
            RIGHTBLUE = 見た目上、向かって右が青

        beam_truth:
            True  = 見たまま
            False = 青/紫を左右反転
        """
        beam_color = str(beam_color).upper()
        beam_truth = bool(beam_truth)

        if beam_color not in ("LEFTBLUE", "RIGHTBLUE"):
            raise ValueError("beam_color must be LEFTBLUE or RIGHTBLUE")

        # まず「見た目通り」の左右色を決める
        if beam_color == "LEFTBLUE":
            left = "blue"
            right = "purple"
        else:
            left = "purple"
            right = "blue"

        # 偽なら反転
        if not beam_truth:
            left, right = right, left

        return left, right

    def _rebuild_exdeath_beam_floor(self):
        """現在の beam_color / beam_truth に応じて床全体を1Geomで作り直す。"""
        if (
            self.exdeath_beam_floor_node is not None
            and not self.exdeath_beam_floor_node.isEmpty()
        ):
            self.exdeath_beam_floor_node.removeNode()

        state = self.exdeath_beam_floor_state
        params = self.exdeath_beam_floor_params

        left_name, right_name = self._resolve_exdeath_beam_floor_colors(
            state["beam_color"],
            state["beam_truth"],
        )

        color_map = {
            "blue": params["blue_color"],
            "purple": params["purple_color"],
        }

        self.exdeath_beam_floor_node = (
            self._create_exdeath_beam_floor_combined_geom(
                "exdeath_beam_floor_combined",
                left_color=color_map[left_name],
                right_color=color_map[right_name],
                center_color=params["red_color"],
            )
        )

        self._apply_exdeath_beam_floor_visibility()

    def set_exdeath_beam_floor(
        self,
        beam_color=None,
        beam_truth=None,
        visible=None,
    ):
        """
        エクスデスビーム床の色・真偽・表示状態を更新する。
        """
        if beam_color is not None:
            beam_color = str(beam_color).upper()
            if beam_color not in ("LEFTBLUE", "RIGHTBLUE"):
                raise ValueError("beam_color must be LEFTBLUE or RIGHTBLUE")
            self.pattern["beam_color"] = beam_color
            self.exdeath_beam_floor_state["beam_color"] = beam_color

        if beam_truth is not None:
            beam_truth = bool(beam_truth)
            self.pattern["beam_truth"] = beam_truth
            self.exdeath_beam_floor_state["beam_truth"] = beam_truth

        if visible is not None:
            self.exdeath_beam_floor_state["visible"] = bool(visible)

        self._rebuild_exdeath_beam_floor()
        self.update_exdeath_beam_floor_orientation()

    def set_exdeath_beam_floor_result(
        self,
        beam_color_result,
        visible=True,
    ):
        """
        derived["beam_color_result"] をそのまま床の左右色として使う。

        beam_color_result:
            LEFTBLUE  -> 左青 / 右紫
            RIGHTBLUE -> 左紫 / 右青

        ここではpatternの beam_color / beam_truth は変更しない。
        """
        result = str(beam_color_result).upper()
        if result not in ("LEFTBLUE", "RIGHTBLUE"):
            raise ValueError(
                "beam_color_result must be LEFTBLUE or RIGHTBLUE"
            )

        self.exdeath_beam_floor_state["beam_color"] = result
        # 結果値をそのまま表示するので、床側では反転なし(True)として描画。
        self.exdeath_beam_floor_state["beam_truth"] = True
        self.exdeath_beam_floor_state["visible"] = bool(visible)

        self._rebuild_exdeath_beam_floor()
        self.update_exdeath_beam_floor_orientation()

    def set_exdeath_beam_truth(self, beam_truth):
        """beam_truth だけを更新する。"""
        self.set_exdeath_beam_floor(
            beam_truth=beam_truth,
            visible=self.exdeath_beam_floor_state["visible"],
        )

    def show_exdeath_beam_floor(self):
        self.exdeath_beam_floor_state["visible"] = True
        self._apply_exdeath_beam_floor_visibility()

    def hide_exdeath_beam_floor(self):
        self.exdeath_beam_floor_state["visible"] = False
        self._apply_exdeath_beam_floor_visibility()

    def _apply_exdeath_beam_floor_visibility(self):
        if self.exdeath_beam_floor_state["visible"]:
            self.exdeath_beam_floor_root.show()
        else:
            self.exdeath_beam_floor_root.hide()

    def update_exdeath_beam_floor_orientation(self):
        """
        フィールド中心→エクスデス方向をローカル+Yに合わせる。
        これにより「向かって左/右」がエクスデスの出現方向へ追従する。
        """
        if not hasattr(self, "exdeath_beam_floor_root"):
            return

        exdeath_pos = self.enemies["exdeath"].getPos(self.render)

        dx = exdeath_pos.x
        dy = exdeath_pos.y

        if abs(dx) < 0.0001 and abs(dy) < 0.0001:
            return

        # Panda3D H=0 は +Y 向き。
        # +Y から (dx,dy) への角度を求める。
        heading = math.degrees(math.atan2(-dx, dy))
        self.exdeath_beam_floor_root.setH(heading)

    def setup_exdeath_truth_effect(self):
        """
        エクスデス正面の真偽エフェクトを作成する。

        ・リング線なし
        ・エクスデス正面の縦円軌道上を玉2個が公転
        ・2個は180度反対位置
        ・正面から見て反時計回り
        ・玉サイズは現在のカオスと同じ
        ・真  : くすんだ青
        ・偽  : あずき色 + 黄色の「?」
        ・エクスデスの方角変更時はエフェクトも追従する
        """
        self.exdeath_truth_effect = {
            "visible": False,
            "truth": True,
            "angle": 0.0,
            "speed": -55.0,   # deg/sec（正面から見て反時計回り）
        }

        # 現在のカオスと同じ玉サイズ・?表示サイズを使用。
        # orbit_radius はエクスデス本体サイズに合わせて少し大きめ。
        self.exdeath_effect_params = {
            "orbit_radius": 0.80,
            "center_z": 1.40,
            "orb_scale": 0.30,
            "front_offset": 3.0,
            "question_front_offset": 0.34,
            "question_z_offset": -0.07,
        }

        # エクスデス本体の子にしておくことで、表示/非表示や移動に追従させる。
        self.exdeath_effect_root = self.enemies["exdeath"].attachNewNode(
            "exdeath_truth_effect_root"
        )

        self.exdeath_truth_orbs = []

        for index in range(2):
            orb_root = self.exdeath_effect_root.attachNewNode(
                f"exdeath_truth_orb_{index}"
            )

            # 玉本体
            orb = self.loader.loadModel("models/misc/sphere")
            orb.reparentTo(orb_root)
            orb.setScale(self.exdeath_effect_params["orb_scale"])
            orb.setColor(0.30, 0.46, 0.56, 1.0)

            # 偽のときだけ使う「?」
            tn = TextNode(f"exdeath_question_{index}")
            tn.setText("?")
            tn.setAlign(TextNode.ACenter)
            tn.setTextColor(1.0, 0.90, 0.10, 1.0)

            question = orb_root.attachNewNode(tn)
            question.setBillboardPointEye()
            question.setPos(0, 0, 0)
            question.setScale(0.52)
            question.hide()

            self.exdeath_truth_orbs.append({
                "root": orb_root,
                "orb": orb,
                "question": question,
            })

        # 起動直後はいったん非表示。
        self.hide_exdeath_truth_effect()

        # 現在のエクスデス位置に合わせて正面方向を設定。
        self.update_exdeath_effect_orientation()

    def set_exdeath_truth_effect(self, visible=True, truth=True):
        """
        エクスデス真偽エフェクトの表示有無と真偽を設定する。

        truth=True  -> くすんだ青玉
        truth=False -> あずき色玉 + 黄色「?」
        """
        if visible:
            self.exdeath_effect_root.show()

        self.exdeath_truth_effect["visible"] = bool(visible)
        self.exdeath_truth_effect["truth"] = bool(truth)

        self._apply_exdeath_truth_visual()
        self._apply_exdeath_truth_visibility()

    def set_exdeath_truth(self, truth):
        """エクスデスの真偽だけを変更する。"""
        self.exdeath_truth_effect["truth"] = bool(truth)
        self._apply_exdeath_truth_visual()

    def show_exdeath_truth_effect(self):
        """エクスデス真偽エフェクトを表示する。"""
        self.exdeath_effect_root.show()
        self.exdeath_truth_effect["visible"] = True
        self._apply_exdeath_truth_visibility()

    def hide_exdeath_truth_effect(self):
        """エクスデス真偽エフェクトを非表示にする。"""
        self.exdeath_truth_effect["visible"] = False
        self._apply_exdeath_truth_visibility()

    def _apply_exdeath_truth_visual(self):
        truth = self.exdeath_truth_effect["truth"]

        if truth:
            orb_color = (0.30, 0.46, 0.56, 1.0)
        else:
            orb_color = (0.50, 0.20, 0.24, 1.0)

        for orb_info in self.exdeath_truth_orbs:
            orb_info["orb"].setColor(*orb_color)

            if truth:
                orb_info["question"].hide()
            else:
                orb_info["question"].show()

    def _apply_exdeath_truth_visibility(self):
        visible = self.exdeath_truth_effect["visible"]

        for orb_info in self.exdeath_truth_orbs:
            if visible:
                orb_info["root"].show()
            else:
                orb_info["root"].hide()

    def update_exdeath_effect_orientation(self):
        """
        エクスデスの現在位置に応じて、
        真偽エフェクトを常にフィールド中央側の「正面」へ向ける。
        """
        if not hasattr(self, "exdeath_effect_root"):
            return

        exdeath_pos = self.enemies["exdeath"].getPos(self.render)

        # エクスデス → フィールド中央への水平ベクトル
        inward = Vec3(
            -exdeath_pos.x,
            -exdeath_pos.y,
            0.0,
        )

        if inward.lengthSquared() <= 0.0001:
            return

        inward.normalize()

        front_offset = self.exdeath_effect_params["front_offset"]

        # エフェクトの中心をエクスデス正面へ移動
        self.exdeath_effect_root.setPos(
            inward.x * front_offset,
            inward.y * front_offset,
            0.0,
        )

        # エフェクトのローカルY軸を中央方向へ向ける。
        # Zを変えないことが重要。
        effect_world = self.exdeath_effect_root.getPos(self.render)

        target_world = Vec3(
            effect_world.x + inward.x,
            effect_world.y + inward.y,
            effect_world.z,
        )

        self.exdeath_effect_root.lookAt(
            self.render,
            target_world,
        )

    def update_exdeath_truth_effect(self, dt):
        """エクスデス正面の縦円軌道上で2個の玉を反時計回りに公転させる。"""
        if not hasattr(self, "exdeath_truth_effect"):
            return

        state = self.exdeath_truth_effect
        if not state["visible"]:
            return

        # 方角変更後も正面に追従するよう毎フレーム整える。
        self.update_exdeath_effect_orientation()

        state["angle"] = (
            state["angle"] + state["speed"] * dt
        ) % 360.0

        orbit_radius = self.exdeath_effect_params["orbit_radius"]
        center_z = self.exdeath_effect_params["center_z"]

        for index, orb_info in enumerate(self.exdeath_truth_orbs):
            angle = state["angle"] + index * 180.0
            rad = math.radians(angle)

            # effect_root のローカルX-Z平面上で縦円を作る。
            x = math.cos(rad) * orbit_radius
            z = center_z + math.sin(rad) * orbit_radius

            orb_info["root"].setPos(x, 0.0, z)

            # 偽の「?」は玉の正面（カメラ側）へ少し手前に出す。
            if not state["truth"]:
                orb_world = orb_info["root"].getPos(self.render)
                cam_world = self.camera.getPos(self.render)
                to_camera = cam_world - orb_world

                if to_camera.lengthSquared() > 0.0001:
                    to_camera.normalize()

                    question_world = (
                        orb_world
                        + to_camera
                        * self.exdeath_effect_params["question_front_offset"]
                    )
                    question_world.z += (
                        self.exdeath_effect_params["question_z_offset"]
                    )

                    orb_info["question"].setPos(
                        self.render,
                        question_world.x,
                        question_world.y,
                        question_world.z,
                    )

    def create_inverted_cone_enemy(
        self,
        enemy_id,
        label,
        color,
        radius=0.85,
        height=2.2,
        segments=48,
    ):
        """
        当たり判定を持たない敵表示用の逆円錐。
        上面が円、下側中央が頂点。
        radius / height は従来の円柱と同じ値を流用する。
        """
        root = self.render.attachNewNode(f"enemy_{enemy_id}")

        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(
            f"{enemy_id}_inverted_cone",
            fmt,
            Geom.UHStatic,
        )
        vw = GeomVertexWriter(vdata, "vertex")
        cw = GeomVertexWriter(vdata, "color")

        top_color = (
            min(color[0] * 1.18, 1.0),
            min(color[1] * 1.18, 1.0),
            min(color[2] * 1.18, 1.0),
            color[3],
        )

        # 上面円周
        for i in range(segments + 1):
            t = 2.0 * math.pi * i / segments
            x = math.cos(t) * radius
            y = math.sin(t) * radius

            vw.addData3f(x, y, height)
            cw.addData4f(*color)

        # 下側の頂点
        apex_index = segments + 1
        vw.addData3f(0.0, 0.0, 0.0)
        cw.addData4f(*color)

        # 上面中心
        top_center_index = segments + 2
        vw.addData3f(0.0, 0.0, height)
        cw.addData4f(*top_color)

        tris = GeomTriangles(Geom.UHStatic)

        # 側面
        for i in range(segments):
            tris.addVertices(i, apex_index, i + 1)

        # 上面
        # 上から見たときに表面になるよう頂点順を修正
        for i in range(segments):
            tris.addVertices(top_center_index, i, i + 1)

        geom = Geom(vdata)
        geom.addPrimitive(tris)

        node = GeomNode(f"{enemy_id}_geom")
        node.addGeom(geom)
        root.attachNewNode(node)

        # 敵名ラベルは表示しない。
        # 詠唱名は詠唱バー側で表示する。
        return root

    def create_cylinder_enemy(self, enemy_id, label, color, radius=0.85, height=2.2, segments=48):
        """当たり判定を持たない敵表示用の単純な円柱。"""
        root = self.render.attachNewNode(f"enemy_{enemy_id}")

        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(f"{enemy_id}_cylinder", fmt, Geom.UHStatic)
        vw = GeomVertexWriter(vdata, "vertex")
        cw = GeomVertexWriter(vdata, "color")

        top_color = (
            min(color[0] * 1.18, 1.0),
            min(color[1] * 1.18, 1.0),
            min(color[2] * 1.18, 1.0),
            color[3],
        )

        for i in range(segments + 1):
            t = 2 * math.pi * i / segments
            x, y = math.cos(t) * radius, math.sin(t) * radius
            vw.addData3f(x, y, 0.0); cw.addData4f(*color)
            vw.addData3f(x, y, height); cw.addData4f(*color)

        side = GeomTriangles(Geom.UHStatic)
        for i in range(segments):
            i0 = i * 2
            side.addVertices(i0, i0 + 2, i0 + 1)
            side.addVertices(i0 + 2, i0 + 3, i0 + 1)

        top_center = (segments + 1) * 2
        vw.addData3f(0, 0, height); cw.addData4f(*top_color)
        top_start = top_center + 1
        for i in range(segments + 1):
            t = 2 * math.pi * i / segments
            x, y = math.cos(t) * radius, math.sin(t) * radius
            vw.addData3f(x, y, height); cw.addData4f(*top_color)

        top = GeomTriangles(Geom.UHStatic)
        for i in range(segments):
            top.addVertices(top_center, top_start + i, top_start + i + 1)

        geom = Geom(vdata)
        geom.addPrimitive(side)
        geom.addPrimitive(top)
        node = GeomNode(f"{enemy_id}_geom")
        node.addGeom(geom)
        root.attachNewNode(node)

        # 敵名ラベルは表示しない。
        # 詠唱名は詠唱バー側で表示する。
        return root

    def setup_supplemental_audio(self):
        """
        Excel「補助音声一覧」の定義を保持する。
        実ファイルは必要になった時点で ./sound から遅延ロードする。
        """
        self.sound_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "sound",
        )
        self.supplemental_sound_cache = {}

        self.supplemental_sound_files = {
            "gc_floor": {
                "NONE": "none.wav",
                "THUNDER": "thunder.wav",
                "BLIZZARD": "blizzard.wav",
                "BOTH": "both.wav",
            },
            "exdeath_truth": {
                True: "exdeath_true.wav",
                False: "exdeath_false.wav",
            },
            "beam_truth": {
                True: "beam_true.wav",
                False: "beam_false.wav",
            },
            "shriek_truth": {
                True: "shriek_true.wav",
                False: "shriek_false.wav",
            },
            "fire_result": {
                "CHARIOT": "fire_chariot.wav",
                "DYNAMO": "fire_dynamo.wav",
            },
            "tsunami_result": {
                "DYNAMO": "tsunami_dynamo.wav",
                "CHARIOT": "tsunami_chariot.wav",
            },
            "magic_charge_floor": {
                True: "magiccharge_true.wav",
                False: "magiccharge_false.wav",
            },
            "magic_out_result": {
                "NONE": "none.wav",
                "THUNDER": "thunder.wav",
                "BLIZZARD": "blizzard.wav",
                "BOTH": "both.wav",
            },
        }

        # Reset時に、Reset前から予約されていた遅延音声を無効化するための世代番号。
        self.audio_generation = 0

    def _get_supplemental_sound(self, filename):
        """./sound/filename を必要時に読み込み、キャッシュして返す。"""
        if filename in self.supplemental_sound_cache:
            return self.supplemental_sound_cache[filename]

        path = os.path.join(self.sound_dir, filename)

        if not os.path.exists(path):
            print(f"[sound] file not found: {path}")
            self.supplemental_sound_cache[filename] = None
            return None

        sound = self.loader.loadSfx(
            Filename.fromOsSpecific(path).getFullpath()
        )

        if sound is None:
            print(f"[sound] could not load: {path}")

        self.supplemental_sound_cache[filename] = sound
        return sound

    def _play_supplemental_sound(self, filename):
        """GUI音量を反映して1ファイル再生する。"""
        sound = self._get_supplemental_sound(filename)
        if sound is None:
            return None

        sound.setVolume(max(0.0, min(1.0, self.volume / 100.0)))
        sound.play()
        return sound

    def _delayed_supplemental_sound_task(
        self,
        filename,
        generation,
        task,
    ):
        """
        予約した補助音声を再生する。
        Reset後はgenerationが変わるため、古い予約音声は鳴らさない。
        """
        if generation != self.audio_generation:
            return Task.done

        self._play_supplemental_sound(filename)
        return Task.done

    def play_gc_floor_call_slot(self, floor_result):
        """
        GC床だけのコール枠。
        「GC床」チェックONなら音声を出し、OFFなら無音枠として扱う。
        """
        floor_filename = self.supplemental_sound_files[
            "gc_floor"
        ].get(floor_result)

        if not floor_filename:
            return

        floor_sound = self._get_supplemental_sound(
            floor_filename
        )
        if floor_sound is None:
            return

        if self.gui_flags.get("call_gc_floor", False):
            floor_sound.setVolume(
                max(0.0, min(1.0, self.volume / 100.0))
            )
            floor_sound.play()

    def play_gc_floor_then_exdeath_call(
        self,
        floor_result,
        exdeath_truth,
        gap=1.5,
    ):
        """
        GC床 → エクスデス真偽 の連続コール枠。

        GUIチェックは「そのコール枠で音を出すかどうか」だけを決める。
        チェックOFFでもコール枠そのものは消さない。

        したがってGC床チェックOFFでも、
            本来のGC床音声の長さ
            + gap秒
        を待ってからエクスデス真偽のコール枠へ進む。

        これにより、
        ・GC床ON  : 音声を聞いて処理する
        ・GC床OFF : 同じ時間枠で自分がコールする
        のどちらでも、その後のタイミングが変化しない。
        """
        floor_enabled = self.gui_flags.get("call_gc_floor", False)
        exdeath_enabled = self.gui_flags.get(
            "call_exdeath_truth",
            False,
        )

        floor_filename = self.supplemental_sound_files[
            "gc_floor"
        ].get(floor_result)

        exdeath_filename = self.supplemental_sound_files[
            "exdeath_truth"
        ][bool(exdeath_truth)]

        # ------------------------------------------------------
        # GC床コール枠
        # ------------------------------------------------------
        # ON/OFFにかかわらず音声をロードして長さを取得する。
        # OFFの場合は再生しないが、この長さの「無音枠」は保持する。
        floor_sound = None
        floor_duration = 0.0

        if floor_filename:
            floor_sound = self._get_supplemental_sound(
                floor_filename
            )

            if floor_sound is not None:
                floor_duration = max(
                    0.0,
                    float(floor_sound.length()),
                )

                if floor_enabled:
                    volume = max(
                        0.0,
                        min(1.0, self.volume / 100.0),
                    )
                    floor_sound.setVolume(volume)
                    floor_sound.play()

        # ------------------------------------------------------
        # エクスデス真偽コール枠
        # ------------------------------------------------------
        # GC床音声を実際に鳴らしたかどうかには依存しない。
        # 常に「GC床の本来の長さ + gap」の後にこの枠が来る。
        delay = floor_duration + max(0.0, float(gap))

        if exdeath_enabled:
            if delay <= 0.0:
                self._play_supplemental_sound(exdeath_filename)
            else:
                task_name = "supplemental_exdeath_truth_after_gc"
                self.taskMgr.remove(task_name)
                self.taskMgr.doMethodLater(
                    delay,
                    self._delayed_supplemental_sound_task,
                    task_name,
                    extraArgs=[
                        exdeath_filename,
                        self.audio_generation,
                    ],
                    appendTask=True,
                )

        # exdeath_enabled == False の場合も、
        # エクスデス真偽のコール枠自体は存在する。
        # ただし、この関数の後続処理はタイムライン時刻で管理されるため、
        # ここでは音を出さないだけでよい。


    def play_tsunami_then_magic_out_call(self, gap=0.3):
        """
        つなみ発動時コール
        → つなみ音声の本来の長さ
        → gap秒
        → Magic Outコール

        各チェックがOFFでも、そのコール枠の時間は保持する。
        """
        tsunami_enabled = self.gui_flags.get(
            "call_fire_tsunami",
            False,
        )
        magic_out_enabled = self.gui_flags.get(
            "call_magic_out",
            False,
        )

        tsunami_filename = self.supplemental_sound_files[
            "tsunami_result"
        ][self.derived["tsunami_result"]]

        magic_out_filename = self.supplemental_sound_files[
            "magic_out_result"
        ][self.derived["magic_out_result"]]

        tsunami_sound = self._get_supplemental_sound(
            tsunami_filename
        )
        tsunami_duration = 0.0

        if tsunami_sound is not None:
            tsunami_duration = max(
                0.0,
                float(tsunami_sound.length()),
            )

            if tsunami_enabled:
                tsunami_sound.setVolume(
                    max(0.0, min(1.0, self.volume / 100.0))
                )
                tsunami_sound.play()

        delay = tsunami_duration + max(0.0, float(gap))

        if magic_out_enabled:
            task_name = "supplemental_magic_out_after_tsunami"
            self.taskMgr.remove(task_name)

            if delay <= 0.0:
                self._play_supplemental_sound(
                    magic_out_filename
                )
            else:
                self.taskMgr.doMethodLater(
                    delay,
                    self._delayed_supplemental_sound_task,
                    task_name,
                    extraArgs=[
                        magic_out_filename,
                        self.audio_generation,
                    ],
                    appendTask=True,
                )

    def stop_supplemental_audio(self):
        """Reset時などに、再生中/予約中の補助音声を止める。"""
        self.audio_generation += 1
        self.taskMgr.remove("supplemental_exdeath_truth_after_gc")
        self.taskMgr.remove("supplemental_magic_out_after_tsunami")

        for sound in self.supplemental_sound_cache.values():
            if sound is not None:
                sound.stop()

    def setup_positional_audio(self):
        """
        エクスデス再登場SEなど、方向を感じたい効果音用の3D音響。
        リスナーはカメラ、音源はエクスデスNodeに追従させる。
        """
        self.audio3d = Audio3DManager(
            self.sfxManagerList[0],
            self.camera,
        )
        # 距離減衰で聞こえなくなるのを避け、
        # 主に左右/方向感の手がかりとして使う。
        self.audio3d.setDropOffFactor(0.0)

        self.positional_sound_cache = {}

    def _get_positional_sound(self, filename, node):
        """3D効果音をロードして指定Nodeへ追従させる。"""
        cache_key = (filename, node.getName())

        if cache_key in self.positional_sound_cache:
            return self.positional_sound_cache[cache_key]

        path = os.path.join(self.sound_dir, filename)
        if not os.path.exists(path):
            print(f"[sound] file not found: {path}")
            self.positional_sound_cache[cache_key] = None
            return None

        sound = self.audio3d.loadSfx(
            Filename.fromOsSpecific(path).getFullpath()
        )
        if sound is None:
            print(f"[sound] could not load: {path}")
            self.positional_sound_cache[cache_key] = None
            return None

        self.audio3d.attachSoundToObject(sound, node)
        sound.setVolume(max(0.0, min(1.0, self.volume / 100.0)))

        self.positional_sound_cache[cache_key] = sound
        return sound

    def _get_exdeath_camera_relative_frontness(self):
        """
        カメラ基準でエクスデスが前後どちらにいるかを -1～+1 で返す。

        +1 = 真正面、0 = 真横、-1 = 真後ろ。
        絶対方角ではなく camera_yaw を基準にする。
        """
        exdeath_pos = self.enemies["exdeath"].getPos(self.render)
        player_pos = self.player.getPos(self.render)

        to_exdeath = Vec2(
            exdeath_pos.x - player_pos.x,
            exdeath_pos.y - player_pos.y,
        )
        if to_exdeath.lengthSquared() < 0.000001:
            return 1.0
        to_exdeath.normalize()

        yaw_rad = math.radians(self.camera_yaw)
        camera_forward = Vec2(
            math.sin(yaw_rad),
            math.cos(yaw_rad),
        )

        return max(-1.0, min(1.0, to_exdeath.dot(camera_forward)))

    def _get_exdeath_move_volume_factor(self):
        """
        エクスデス再登場SEの前後方向による音量係数を返す。

        真正面: 0.55
        真横  : 0.775
        真後ろ: 1.00

        その間は連続的に補間する。
        """
        frontness = self._get_exdeath_camera_relative_frontness()
        rear_factor = (1.0 - frontness) * 0.5
        return 0.55 + 0.45 * rear_factor

    def update_exdeath_move_front_back_cue(self):
        """
        再登場SEの再生中、カメラ基準の前後位置に応じて音量を更新する。

        音程や左右定位には手を加えない。
        左右方向は Audio3DManager 本来の3D定位をそのまま使用する。
        """
        if not hasattr(self, "positional_sound_cache"):
            return

        cache_key = (
            "exdeath_move.wav",
            self.enemies["exdeath"].getName(),
        )
        sound = self.positional_sound_cache.get(cache_key)
        if sound is None:
            return

        master_volume = max(0.0, min(1.0, self.volume / 100.0))
        sound.setVolume(
            master_volume * self._get_exdeath_move_volume_factor()
        )

    def play_exdeath_move_sound(self):
        """
        ./sound/exdeath_move.wav を現在のエクスデス位置から3D再生する。
        54.3secで exdeath_direction の位置へ先に移動させてから呼ぶ。

        カメラ正面側では音量を絞り、後方ほど通常音量へ近づける。
        音程と3D左右定位は変更しない。
        """
        sound = self._get_positional_sound(
            "exdeath_move.wav",
            self.enemies["exdeath"],
        )
        if sound is None:
            return

        master_volume = max(0.0, min(1.0, self.volume / 100.0))
        sound.setVolume(
            master_volume * self._get_exdeath_move_volume_factor()
        )
        sound.stop()
        sound.play()

    def setup_enemy_cast_bars(self):
        """
        敵頭上の詠唱名＋詠唱バーを作成する。
        バーは黒背景の上を薄黄色が左→右へ伸びる。
        """
        self.enemy_casts = {}

        # 3D空間上でのバー寸法
        # 敵ごとの詠唱バーサイズ。
        self.cast_bar_sizes = {
            "kefka":   (1.50, 0.070),
            "chaos":   (3.00, 0.140),
            "exdeath": (3.00, 0.140),
        }
        self.cast_text_scales = {
            "kefka": 0.15,
            "chaos": 0.30,
            "exdeath": 0.30,
        }
        self.cast_bar_gap = 0.04
        self.cast_color = (1.0, 0.92, 0.58, 1.0)
        self.cast_bg_color = (0.015, 0.015, 0.015, 0.96)

        # 敵ごとの頭上位置。必要なら後で個別調整可能。
        self.cast_height_offsets = {
            "kefka": 2.50,
            "chaos": 2.80,
            "exdeath": 2.80,
        }

        cm = CardMaker("cast_bar_card")
        cm.setFrame(-0.5, 0.5, -0.5, 0.5)

        for enemy_id, enemy in self.enemies.items():
            bar_width, bar_height = self.cast_bar_sizes[enemy_id]
            text_scale = self.cast_text_scales[enemy_id]

            root = self.render.attachNewNode(f"{enemy_id}_cast_root")
            root.setPos(
                enemy.getX(),
                enemy.getY(),
                enemy.getZ() + self.cast_height_offsets[enemy_id],
            )

            # 常にカメラ正面を向くビルボード。
            root.setBillboardPointEye()

            # 詠唱名
            cast_name = TextNode(f"{enemy_id}_cast_name")
            cast_name.setAlign(TextNode.ACenter)
            cast_name.setText("")
            cast_name.setTextColor(*self.cast_color)
            cast_name.setFont(self.gui_font)
            name_np = root.attachNewNode(cast_name)
            name_np.setScale(text_scale)
            name_np.setPos(0, 0, 0.17)

            # 黒いバー背景
            bg = root.attachNewNode(cm.generate())
            bg.setColor(*self.cast_bg_color)
            bg.setScale(
                bar_width,
                1.0,
                bar_height,
            )
            bg.setTransparency(TransparencyAttrib.MAlpha)

            # 薄黄色の進捗バー。
            # 左端を固定するため、scale と x を同時に更新する。
            fill = root.attachNewNode(cm.generate())
            fill.setColor(*self.cast_color)
            fill.setScale(0.001, 1.0, bar_height * 0.68)
            fill.setPos(-bar_width * 0.5, -0.002, 0)
            fill.setTransparency(TransparencyAttrib.MAlpha)

            # 少し手前に描画して背景とのちらつきを防ぐ。
            bg.setDepthWrite(False)
            fill.setDepthWrite(False)
            bg.setBin("transparent", 50)
            fill.setBin("transparent", 51)

            root.hide()

            self.enemy_casts[enemy_id] = {
                "root": root,
                "name_node": cast_name,
                "name_np": name_np,
                "bg": bg,
                "fill": fill,
                "active": False,
                "elapsed": 0.0,
                "duration": 1.0,
                "cast_name": "",
                "bar_width": bar_width,
                "bar_height": bar_height,
            }

    def start_enemy_cast(self, enemy_id, cast_name, duration):
        """指定した敵の詠唱を開始する。"""
        cast = self.enemy_casts.get(enemy_id)
        if cast is None:
            return

        cast["cast_name"] = str(cast_name)
        cast["duration"] = max(0.001, float(duration))
        cast["elapsed"] = 0.0
        cast["active"] = True
        cast["name_node"].setText(cast["cast_name"])
        self._set_enemy_cast_progress(enemy_id, 0.0)
        cast["root"].show()

    def stop_enemy_cast(self, enemy_id, hide=True):
        """詠唱を停止する。hide=Trueならバーも消す。"""
        cast = self.enemy_casts.get(enemy_id)
        if cast is None:
            return
        cast["active"] = False
        if hide:
            cast["root"].hide()

    def _set_enemy_cast_progress(self, enemy_id, progress):
        """0.0～1.0で詠唱バーの進捗を設定する。"""
        cast = self.enemy_casts[enemy_id]
        p = max(0.0, min(1.0, float(progress)))

        bar_width = cast["bar_width"]
        bar_height = cast["bar_height"]

        width = bar_width * p
        # CardMakerは中心基準なので、左端を固定したまま右へ伸ばす。
        cast["fill"].setScale(
            max(width, 0.001),
            1.0,
            bar_height * 0.68,
        )
        cast["fill"].setX(
            -bar_width * 0.5 + width * 0.5
        )

    def update_enemy_cast_bars(self, dt):
        """詠唱時間を進め、敵の移動にも追従させる。"""
        for enemy_id, cast in self.enemy_casts.items():
            enemy = self.enemies[enemy_id]

            # 敵が移動しても頭上へ追従。
            cast["root"].setPos(
                enemy.getX(),
                enemy.getY(),
                enemy.getZ() + self.cast_height_offsets[enemy_id],
            )

            # 敵本体が非表示なら詠唱バーも見せない。
            if enemy.isHidden():
                cast["root"].hide()
                continue

            if not cast["active"]:
                continue

            cast["root"].show()
            cast["elapsed"] += dt
            progress = cast["elapsed"] / cast["duration"]
            self._set_enemy_cast_progress(enemy_id, progress)

            if progress >= 1.0:
                # 100%到達と同時に詠唱終了。
                # バーと詠唱名は自動で非表示にする。
                cast["active"] = False
                cast["root"].hide()

    def setup_enemies(self):
        """
        ケフカ中央、カオス北西、エクスデス北東の敵オブジェクトを作成する。

        ・ケフカ: 中央固定
        ・カオス: 北西、フィールド外周より外側
        ・エクスデス: デバッグ表示時は北東。
          ランダム配置が必要なギミックでは
          self.pattern["exdeath_direction"] を使用する。
        """
        self.enemies = {}

        # カオスとエクスデスはフィールド外へ配置。
        chaos_r = self.field_radius * 1.08
        exdeath_r = self.field_radius * 1.18

        chaos_diag = chaos_r / math.sqrt(2.0)
        exdeath_diag = exdeath_r / math.sqrt(2.0)

        # --- ケフカ ---
        self.enemies["kefka"] = self.create_inverted_cone_enemy(
            "kefka",
            "ケフカ",
            (0.78, 0.38, 0.42, 1.0),
            0.82,
            2.15,
        )
        self.enemies["kefka"].setPos(0, 0, 0.02)

        # --- カオス ---
        self.enemies["chaos"] = self.create_cylinder_enemy(
            "chaos",
            "カオス",
            (0.82, 0.59, 0.05, 1.0),
            1.425,
            2.45,
        )
        self.enemies["chaos"].setPos(
            -chaos_diag,
            chaos_diag,
            0.02,
        )

        # --- エクスデス ---
        # 旧直径の2倍 → 半径 0.95 * 2
        self.enemies["exdeath"] = self.create_cylinder_enemy(
            "exdeath",
            "エクスデス",
            (0.30, 0.38, 0.58, 1.0),
            2.5,
            2.45,
        )

        # エクスデス用8方向座標。
        # カオスより少し外側の半径を使用。
        self.exdeath_positions = {
            "N": Vec3(0, exdeath_r, 0.02),
            "NE": Vec3(exdeath_diag, exdeath_diag, 0.02),
            "E": Vec3(exdeath_r, 0, 0.02),
            "SE": Vec3(exdeath_diag, -exdeath_diag, 0.02),
            "S": Vec3(0, -exdeath_r, 0.02),
            "SW": Vec3(-exdeath_diag, -exdeath_diag, 0.02),
            "W": Vec3(-exdeath_r, 0, 0.02),
            "NW": Vec3(-exdeath_diag, exdeath_diag, 0.02),
        }

        # デバッグ用の初期表示は北東のまま。
        # 実際のランダム8方向ギミックでは、
        # self.set_exdeath_direction(self.pattern["exdeath_direction"])
        # を呼ぶ。
        self.exdeath_direction = "NE"
        self.set_exdeath_direction("NE")

        # 起動時の最終表示状態は apply_timeline_initial_state() で設定する。

    def show_enemy(self, enemy_id):
        enemy = self.enemies.get(enemy_id)
        if enemy:
            enemy.show()

    def hide_enemy(self, enemy_id):
        enemy = self.enemies.get(enemy_id)
        if enemy:
            enemy.hide()

    def show_all_enemies(self):
        for enemy in self.enemies.values():
            enemy.show()

    def hide_all_enemies(self):
        for enemy in self.enemies.values():
            enemy.hide()

    def set_exdeath_direction(self, direction):
        direction = direction.upper()
        if direction not in self.exdeath_positions:
            raise ValueError("direction must be N, NE, E, SE, S, SW, W, or NW")
        self.exdeath_direction = direction
        self.enemies["exdeath"].setPos(self.exdeath_positions[direction])

        # 真偽エフェクト作成済みなら、移動先でも正面へ追従させる。
        if hasattr(self, "exdeath_effect_root"):
            self.update_exdeath_effect_orientation()

        # 左右ビームもエクスデスの新しい方角へ追従させる。
        if hasattr(self, "exdeath_beam_root"):
            self.update_exdeath_beam_orientation()

        # ビーム床もエクスデス方向へ追従させる。
        if hasattr(self, "exdeath_beam_floor_root"):
            self.update_exdeath_beam_floor_orientation()

        # 頭割り判定範囲デバッグ表示も同じ軸へ追従。
        if hasattr(self, "stack_area_debug_root"):
            self.update_stack_area_debug()

    def set_exdeath_random_direction(self):
        """
        エクスデス方角パラメータを8方向から再抽選し、
        その方角へエクスデスを配置する。
        """
        direction = random.choice(
            ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        )
        self.pattern["exdeath_direction"] = direction
        self.set_exdeath_direction(direction)
        return direction

    def create_ground_circle_outline(
        self,
        name: str,
        radius: float,
        color=(1, 1, 1, 1),
        segments=64,
        thickness=2.5,
    ):
        ls = LineSegs(name)
        ls.setThickness(thickness)
        ls.setColor(*color)

        for i in range(segments + 1):
            t = (2.0 * math.pi * i) / segments
            x = math.cos(t) * radius
            y = math.sin(t) * radius
            z = 0.0
            if i == 0:
                ls.moveTo(x, y, z)
            else:
                ls.drawTo(x, y, z)

        node = ls.create()
        return self.render.attachNewNode(node)

    def add_field_marker(
        self,
        marker_id: str,
        pos: Vec2,
        ground_shape: str,
        label: str,
        color=(1, 1, 1, 1),
    ):
        root = self.render.attachNewNode(f"marker_{marker_id}")
        root.setPos(pos.x, pos.y, 0.0)

        if ground_shape == "circle":
            ground = self.create_ground_circle_outline(
                name=f"ground_marker_{marker_id}",
                radius=0.45,
                color=color,
                segments=64,
                thickness=2.5,
            )
            ground.reparentTo(root)
            ground.setPos(0, 0, 0.02)
            ground.setTransparency(TransparencyAttrib.MAlpha)

        elif ground_shape == "square":
            s = 0.45
            ls = LineSegs(f"square_{marker_id}")
            ls.setThickness(2.5)
            ls.setColor(*color)
            ls.moveTo(-s, -s, 0.0)
            ls.drawTo(s, -s, 0.0)
            ls.drawTo(s, s, 0.0)
            ls.drawTo(-s, s, 0.0)
            ls.drawTo(-s, -s, 0.0)
            ground = root.attachNewNode(ls.create())
            ground.setPos(0, 0, 0.02)
            ground.setTransparency(TransparencyAttrib.MAlpha)

        text_np = root.attachNewNode(f"text_{marker_id}")
        tn = TextNode(f"tn_{marker_id}")
        tn.setText(label)
        tn.setAlign(TextNode.ACenter)
        tn.setTextColor(color[0], color[1], color[2], color[3])
        text_geom = text_np.attachNewNode(tn.generate())
        text_geom.setBillboardPointEye()
        text_geom.setPos(0, 0, 1.2)
        text_geom.setScale(0.9)
        text_geom.setTransparency(TransparencyAttrib.MAlpha)

        return root

    def setup_player_facing_indicator(self):
        """
        プレイヤーの向きを示す小さな三角形を作成する。
        三角形の先端が現在の前方。
        """
        self.player_facing_root = self.player.attachNewNode(
            "player_facing_root"
        )

        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(
            "player_facing_triangle",
            fmt,
            Geom.UHStatic,
        )
        vw = GeomVertexWriter(vdata, "vertex")
        cw = GeomVertexWriter(vdata, "color")

        # ローカル+Y方向を前方とする三角形
        points = [
            (0.0, 0.48, 0.04),     # 先端：前方へ長く
            (-0.18, 0.10, 0.04),   # 左後
            (0.18, 0.10, 0.04),    # 右後
        ]
        color = (1.0, 1.0, 1.0, 1.0)

        for x, y, z in points:
            vw.addData3f(x, y, z)
            cw.addData4f(*color)

        tris = GeomTriangles(Geom.UHStatic)
        tris.addVertices(0, 1, 2)

        geom = Geom(vdata)
        geom.addPrimitive(tris)

        gnode = GeomNode("player_facing_triangle")
        gnode.addGeom(geom)

        indicator = self.player_facing_root.attachNewNode(gnode)
        indicator.setTransparency(TransparencyAttrib.MAlpha)
        indicator.setTwoSided(True)

        # プレイヤー本体（水色）と区別しやすいピンク。
        indicator.setColor(1.0, 0.30, 0.60, 1.0)

        # player自体がscale=0.14なので、見やすい大きさに補正
        self.player_facing_root.setScale(5.0)

        self.set_player_heading(self.player_heading)

    def set_player_heading(self, heading):
        """
        プレイヤーの向きを設定する。
        0°=北, 90°=東, 180°=南, 270°=西。
        """
        self.player_heading = float(heading) % 360.0

        if hasattr(self, "player_facing_root"):
            self.player_facing_root.setH(-self.player_heading)

    def get_player_facing_vector(self):
        """現在の向きをXY平面上の単位ベクトルで返す。"""
        rad = math.radians(self.player_heading)
        return Vec2(
            math.sin(rad),
            math.cos(rad),
        )

    def get_player_center_facing_dot(self):
        """
        フィールド中心方向との内積を返す。
        > 0 : 中心側
        < 0 : 外側
        ≈ 0 : ほぼ横向き
        """
        px = self.player.getX()
        py = self.player.getY()
        to_center = Vec2(-px, -py)

        if to_center.lengthSquared() < 0.000001:
            return 0.0

        to_center.normalize()
        return self.get_player_facing_vector().dot(to_center)

    def setup_scene(self):
        # --- 地面 ---
        cm = CardMaker("ground")
        cm.setFrame(-30, 30, -30, 30)
        self.ground = self.render.attachNewNode(cm.generate())
        self.ground.setP(-90)
        self.ground.setPos(0, 0, 0)
        self.ground.setColor(0.18, 0.22, 0.20, 1)

        # --- 黄色の外周円の内側（青みのあるグレー） ---
        self.field_floor = self.create_disc_node(
            name="field_floor",
            radius=self.field_radius,
            color=(0.20, 0.27, 0.32, 1.0),
            z=0.01,
        )

        # --- 黄色の外周円(フィールド) ---
        self.field_ring = self.create_ring_node(
            name="field_ring",
            inner_radius=self.field_radius - 0.04,
            outer_radius=self.field_radius + 0.04,
            segments=256,
            color=(0.9, 0.9, 0.2, 0.95),
        )
        self.field_ring.reparentTo(self.render)
        self.field_ring.setPos(0, 0, 0.03)
        self.field_ring.setTransparency(TransparencyAttrib.MAlpha)

        # --- 黄緑色の内周円(タゲサ) ---
        inner_r = self.field_radius * 0.3
        inner_w = 0.03
        self.inner_ring = self.create_ring_node(
            name="inner_ring",
            inner_radius=inner_r - inner_w,
            outer_radius=inner_r + inner_w,
            segments=256,
            color=(0.7, 1.0, 0.3, 0.9),
        )
        self.inner_ring.reparentTo(self.render)
        self.inner_ring.setPos(0, 0, 0.031)
        self.inner_ring.setTransparency(TransparencyAttrib.MAlpha)

        # --- プレイヤー ---
        self.player = self.loader.loadModel("models/misc/sphere")
        self.player.reparentTo(self.render)
        self.player.setScale(0.14)
        self.player.setPos(0, 0, 0.14)
        self.player.setColor(0.2, 0.7, 1.0, 1)

        # プレイヤー前方を示す三角マーカー
        self.setup_player_facing_indicator()

        self.setBackgroundColor(0.05, 0.07, 0.10, 1)

        # --- フィールドマーカー ---
        self.markers = {}
        r = self.field_radius * 0.6
        diag = r / math.sqrt(2.0)
        marker_defs = [
            ("N_C",  Vec2(0,     r),     "circle", "A", (1.0, 0.1, 0.1, 0.3)),
            ("NE_2", Vec2(diag,  diag),  "square", "2", (1.0, 1.0, 0.2, 0.3)),
            ("E_B",  Vec2(r,     0),     "circle", "B", (1.0, 1.0, 0.2, 0.3)),
            ("SE_3", Vec2(diag, -diag),  "square", "3", (0.4, 0.9, 1.0, 0.3)),
            ("S_C",  Vec2(0,    -r),     "circle", "C", (0.4, 0.9, 1.0, 0.3)),
            ("SW_4", Vec2(-diag, -diag), "square", "4", (0.8, 0.4, 1.0, 0.3)),
            ("W_D",  Vec2(-r,    0),     "circle", "D", (0.8, 0.4, 1.0, 0.3)),
            ("NW_1", Vec2(-diag, diag),  "square", "1", (1.0, 0.1, 0.1, 0.3)),
        ]

        for key, pos, shape, label, color in marker_defs:
            self.markers[key] = self.add_field_marker(
                key, pos, shape, label, color
            )

    def create_disc_node(
        self,
        name: str,
        radius: float,
        color=(1, 1, 1, 1),
        z=0.01,
        segments=128,
    ):
        """地面のXY平面上に塗りつぶし円を作成する。"""
        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(name, fmt, Geom.UHStatic)
        v_writer = GeomVertexWriter(vdata, "vertex")
        c_writer = GeomVertexWriter(vdata, "color")

        # 中心点
        v_writer.addData3f(0.0, 0.0, 0.0)
        c_writer.addData4f(*color)

        # 円周上の頂点
        for i in range(segments + 1):
            t = (2.0 * math.pi * i) / segments
            x = math.cos(t) * radius
            y = math.sin(t) * radius
            v_writer.addData3f(x, y, 0.0)
            c_writer.addData4f(*color)

        tris = GeomTriangles(Geom.UHStatic)
        for i in range(1, segments + 1):
            tris.addVertices(0, i, i + 1)

        geom = Geom(vdata)
        geom.addPrimitive(tris)
        gnode = GeomNode(name)
        gnode.addGeom(geom)

        node = self.render.attachNewNode(gnode)
        node.setPos(0, 0, z)
        node.setTransparency(TransparencyAttrib.MAlpha)
        return node

    def create_ring_node(
        self,
        name: str,
        inner_radius: float,
        outer_radius: float,
        segments: int,
        color,
    ):
        fmt = GeomVertexFormat.getV3cp()
        vdata = GeomVertexData(name, fmt, Geom.UHStatic)
        v_writer = GeomVertexWriter(vdata, "vertex")
        c_writer = GeomVertexWriter(vdata, "color")

        for i in range(segments + 1):
            t = (2.0 * math.pi * i) / segments
            c = math.cos(t)
            s = math.sin(t)
            ix = c * inner_radius
            iy = s * inner_radius
            ox = c * outer_radius
            oy = s * outer_radius

            v_writer.addData3f(ix, iy, 0.0)
            c_writer.addData4f(*color)
            v_writer.addData3f(ox, oy, 0.0)
            c_writer.addData4f(*color)

        tris = GeomTriangles(Geom.UHStatic)
        for i in range(segments):
            i0 = i * 2
            i1 = i0 + 1
            i2 = i0 + 2
            i3 = i0 + 3
            tris.addVertices(i0, i1, i2)
            tris.addVertices(i2, i1, i3)

        geom = Geom(vdata)
        geom.addPrimitive(tris)
        gnode = GeomNode(name)
        gnode.addGeom(geom)

        return self.render.attachNewNode(gnode)

    def on_mouse3_down(self):
        self.mouse_look_active = True
        if self.mouseWatcherNode.hasMouse():
            self.last_mouse_x = self.mouseWatcherNode.getMouseX()
            self.last_mouse_y = self.mouseWatcherNode.getMouseY()

    def on_mouse3_up(self):
        self.mouse_look_active = False

    def set_key(self, key, value):
        self.keys[key] = value

    def update_camera(self, dt: float):
        if self.mouse_look_active and self.mouseWatcherNode.hasMouse():
            mx = self.mouseWatcherNode.getMouseX()
            my = self.mouseWatcherNode.getMouseY()
            dx = mx - self.last_mouse_x
            dy = my - self.last_mouse_y
            self.last_mouse_x = mx
            self.last_mouse_y = my

            self.camera_yaw += dx * self.mouse_sensitivity_x * 60.0
            self.camera_pitch -= dy * self.mouse_sensitivity_y * 60.0

        yaw_rad = math.radians(self.camera_yaw)
        pitch_rad = math.radians(self.camera_pitch)
        px, py, pz = self.player.getPos()
        horiz = self.camera_dist * math.cos(pitch_rad)

        cx = px - math.sin(yaw_rad) * horiz
        cy = py - math.cos(yaw_rad) * horiz
        cz = pz + self.camera_height + self.camera_dist * math.sin(pitch_rad)

        self.camera.setPos(cx, cy, cz)
        self.camera.lookAt(px, py, pz + 1.0)

    def update_player(self, dt: float):
        forward = Vec3(
            math.sin(math.radians(self.camera_yaw)),
            math.cos(math.radians(self.camera_yaw)),
            0,
        )
        right = Vec3(forward.y, -forward.x, 0)
        move = Vec3(0, 0, 0)

        if self.keys["w"]:
            move += forward
        if self.keys["s"]:
            move -= forward
        if self.keys["d"]:
            move += right
        if self.keys["a"]:
            move -= right

        self.player_is_moving = move.length() > 0.001

        if self.player_is_moving:
            move.normalize()

            # 移動方向をそのまま体の向きにする。
            heading = math.degrees(
                math.atan2(move.x, move.y)
            ) % 360.0
            self.set_player_heading(heading)

            self.player.setPos(
                self.player.getPos() + move * self.move_speed * dt
            )

    def update(self, task):
        dt = globalClock.getDt()
        self.update_camera(dt)
        self.update_player(dt)
        self.update_player_head_marker_position()
        self.update_exdeath_move_front_back_cue()
        self.update_activation_effects(dt)
        self.update_kefka_truth_effect(dt)
        self.update_chaos_truth_effect(dt)
        self.update_exdeath_truth_effect(dt)
        self.update_enemy_cast_bars(dt)
        self.update_debuffs(dt)
        self.update_timeline(dt)
        return Task.cont


if __name__ == "__main__":
    app = SimulatorBase()
    app.run()
