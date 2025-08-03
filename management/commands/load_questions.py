# games/management/commands/load_questions.py
from django.core.management.base import BaseCommand
from games.models import GamePackage, LettersGameQuestion

class Command(BaseCommand):
    help = 'إدخال أسئلة لعبة خلية الحروف'

    def handle(self, *args, **options):
        # إنشاء أو جلب الحزمة المجانية
        free_package, created = GamePackage.objects.get_or_create(
            game_type='letters',
            package_number=1,
            defaults={
                'is_free': True,
                'price': 0.00,
                'is_active': True
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS('تم إنشاء الحزمة المجانية')
            )
        else:
            self.stdout.write(
                self.style.WARNING('الحزمة المجانية موجودة، سيتم تحديث الأسئلة')
            )

        # الأسئلة - أسئلتك الحقيقية
        اسئلة = {
            "أ": {
                "رئيسي": ("ما اسم دولة أوروبية عاصمتها تيرانا وتبدأ بحرف الألف؟", "ألبانيا", "بلدان"),
                "بديل1": ("ما اسم وظيفة في المجال الطبي تبدأ بحرف الألف ويعمل صاحبها على تشخيص الأمراض؟", "أخصائي", "وظائف"), 
                "بديل2": ("ما اسم حيوان يعيش في المياه ويُعرف بحركته الانزلاقية ويبدأ بحرف الألف؟", "أخطبوط", "حيوانات")
            },
            "ب": {
                "رئيسي": ("ما اسم وظيفة يعمل فيها الشخص على العناية بالنباتات والزهور؟", "بستاني", "وظائف"),
                "بديل1": ("ما اسم دولة آسيوية تشتهر بصناعة النسيج وعاصمتها دكا؟", "بنغلاديش", "بلدان"),
                "بديل2": ("ما اسم علم يُعنى بدراسة السلوك والعقل؟", "بيولوجيا السلوك", "علوم")
            },
            "ت": {
                "رئيسي": ("ما اسم جهاز يُستخدم لقياس درجة الحرارة ويبدأ بحرف التاء؟", "ترمومتر", "اختراعات"),
                "بديل1": ("ما اسم دولة عربية تشتهر بالصناعات الجلدية وتبدأ بحرف التاء؟", "تونس", "بلدان"),
                "بديل2": ("ما اسم حيوان بري صغير يعيش في الصحراء ويبدأ بحرف التاء؟", "تيس", "حيوانات")
            },
            "ث": {
                "رئيسي": ("ما اسم علم يهتم بدراسة الثقافات والمجتمعات القديمة ويبدأ بحرف الثاء؟", "ثقافة الشعوب", "ثقافي"),
                "بديل1": ("ما الكلمة التي تُستخدم في اللغة العربية لوصف الكثرة الشديدة وتبدأ بحرف الثاء؟", "ثرثرة", "لغوي"),
                "بديل2": ("ما اسم مدينة سعودية تشتهر بالزراعة وتقع في منطقة القصيم وتبدأ بحرف الثاء؟", "ثادق", "مدن سعودية")
            },
            "ج": {
                "رئيسي": ("ما اسم دولة تقع في أمريكا الوسطى وتبدأ بحرف الجيم؟", "جامايكا", "بلدان"),
                "بديل1": ("من هو القائد المسلم الذي قاد جيوش المسلمين في معركة نهاوند ويبدأ اسمه بحرف الجيم؟", "جرير بن عبدالله", "تاريخ"),
                "بديل2": ("ما اسم شاعر عباسي معروف بالفخر والهجاء ويبدأ اسمه بحرف الجيم؟", "جرير", "أدب")
            }
        }

        # مطابقة أنواع الأسئلة
        question_types = {
            "رئيسي": "main",
            "بديل1": "alt1", 
            "بديل2": "alt2"
        }

        # حذف الأسئلة القديمة للحزمة
        LettersGameQuestion.objects.filter(package=free_package).delete()
        self.stdout.write(
            self.style.WARNING('تم حذف الأسئلة القديمة')
        )

        # إدخال الأسئلة الجديدة
        questions_created = 0
        
        for حرف, اسئلة_الحرف in اسئلة.items():
            for نوع_السؤال, (نص_السؤال, الاجابة, التصنيف) in اسئلة_الحرف.items():
                LettersGameQuestion.objects.create(
                    package=free_package,
                    letter=حرف,
                    question_type=question_types[نوع_السؤال],
                    question=نص_السؤال,
                    answer=الاجابة,
                    category=التصنيف
                )
                questions_created += 1
                
                self.stdout.write(
                    f'✓ تم إدخال: {حرف} - {نوع_السؤال} - {نص_السؤال[:30]}...'
                )

        self.stdout.write(
            self.style.SUCCESS(f'تم إدخال {questions_created} سؤال بنجاح!')
        )

        # إنشاء حزم مدفوعة (فارغة للآن)
        for package_num in range(2, 6):  # حزم 2-5
            paid_package, created = GamePackage.objects.get_or_create(
                game_type='letters',
                package_number=package_num,
                defaults={
                    'is_free': False,
                    'price': 10.00,  # 10 ريال للحزمة
                    'is_active': True
                }
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f'تم إنشاء الحزمة المدفوعة رقم {package_num}')
                )

        self.stdout.write(
            self.style.SUCCESS('🎉 تم الانتهاء من إعداد البيانات!')
        )